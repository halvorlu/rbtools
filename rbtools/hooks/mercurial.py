#!/usr/bin/env python2
# coding: utf-8
"""A Mercurial hook to post to ReviewBoard on push to a central server.

The hook was designed to make posting to ReviewBoard easy for new or
inexperienced users. It allows user to post to ReviewBoard by using the
ordinary "hg push", without any need to learn or install RBTools locally.

This hook fits the following workflow:
1. A user makes some (local) commits
2. He pushes those commits to the central server
3. The hook is invoked on the server. The hook checks whether all commits
   have been approved in previous review request(s). If not, it creates
   a new request for the commits (or adds to an existing one).
4. The hook denies the push if not all commits have been approved.
   It approves the push if the commits have been approved, upon which the
   commits are permanently added to the central repository.
5. Users can then (try to) push the changesets again as often as they wish,
   until some has approved the review request and the push succeeds.

In more detail, the hook (step 3-4 above) does the following:
1. Iterates over all incoming changesets, and tries to find a review request
   with the right commit ID.
2. If all commits belong to approved review requests, the push succeeds.
3. If a pending (non-approved) review request is found, any remaining (new)
   changesets are added to this review request, i.e. the description and
   diff are updated, and the push is stopped.

The hook considers a review request to be approved when it has been approved
by someone else, i.e. someone else than the one doing the push.
The hook can allow merges to pass without approval (see configuration below),
in order to avoid the need to review simple merges with commits that entered
the central repository while the review was under way.

Configuration:
The hook is configured through a section in the central repository's hgrc file,
in the [reviewboardhook] section. The following settings are available:

reviewboard_url:
The main base URL for the ReviewBoard server, e.g. http://example.com/

ticket_url:
The URL for the issue/bug/ticket tracker. Any references to issues/bugs/tickets
in the commit messages are linked to this URL as <ticket_url><ticket_id>

allow_merge:
True/1 if merges are automatically approved, False/0 if not (default).

To enable the hook, add the following line in the [hooks] section:
pretxnchangegroup.rb = /path/to/hook/reviewboardhook.py
"""
from rbtools.api.client import RBClient
from rbtools.api.errors import AuthorizationError, APIError
from rbtools.clients.mercurial import MercurialClient
from rbtools.hooks.common import linkify_ticket_refs
from os.path import expanduser, isfile
import logging
import subprocess
import os


HOOK_FAILED = True  # True means error (not equal to zero)
HOOK_SUCCESS = False  # False means OK (equal to zero)


def generate_summary(all_ctx):
    """Generate a summary from a list of changeset ids."""
    summary = extcmd(["hg", "log", "-r", all_ctx[0], "--template",
                      "{desc}"])
    return summary.split("\n")[0]


def generate_description(all_ctx):
    """Generate a description from a list of changeset ids."""
    header = "{0} changesets:".format(len(all_ctx))
    maintext = extcmd(["hg", "log", "-r", all_ctx[0] + ":" + all_ctx[-1],
                       "--template",
                       "{author} ({date|isodate}):\n{desc}\n\n"])
    return header + maintext


def generate_linked_description(all_ctx, ticket_url):
    """Generate a description from a list of changeset ids.

    References to tickets/bugs/issues are linkified with given base URL."""
    text = generate_description(all_ctx)
    text = linkify_ticket_refs(text, ticket_url)
    return text


def update_and_publish(root, ticketurl, all_ctx, revreq, parent=None):
    """Update and publish given review request based on changesets.

    parent is the last commit known by the repository before the push."""
    if parent is None:
        parent = all_ctx[0] + "^1"
    differ = MercurialDiffer(root)
    diff_info = differ.diff(all_ctx[0]+"^1",
                            all_ctx[-1], parent)
    parent_diff = diff_info['parent_diff']
    diffs = revreq.get_diffs(only_links='upload_diff', only_fields='')
    if len(parent_diff) > 0:
        diffs.upload_diff(diff_info['diff'],
                          parent_diff=parent_diff,
                          base_commit_id=
                          diff_info['base_commit_id'])
    else:
        diffs.upload_diff(diff_info['diff'])
    description = unicode(generate_linked_description(all_ctx, ticketurl),
                          'utf-8')
    summary = unicode(generate_summary(all_ctx), 'utf-8')
    commit_id = str(all_ctx[-1])
    draft = revreq.get_draft(only_links='update', only_fields='')
    draft = draft.update(
        summary=summary,
        description=description,
        description_text_type='markdown',
        commit_id=commit_id,
        public=True)


class MercurialDiffer(object):
    """A class to return diffs compatible with server."""
    def __init__(self, root):
        """Initialize object with the given API root."""
        from rbtools.commands import Command
        self.tool = MercurialClient()
        cmd = Command()
        self.tool.capabilities = cmd.get_capabilities(api_root=root)

    def diff(self, rev1, rev2, parent=None):
        """Return a diff_info between rev1 and rev2.

        diff_info['diff'] is the diff between rev1 and rev2
        diff_info['base_commit_id'] is the base of the parent diff
        diff_info['parent_diff'] is the parent diff (between base and rev1)

        parent is the last commit known before the push.
        If parent is unspecified, it is assumed to be rev1."""
        if parent is None:
            parent = rev1
        revisions = {'base': rev1, 'tip': rev2, 'parent_base': parent}
        diff_info = self.tool.diff(revisions=revisions)
        return diff_info


def extcmd(cmd, cwd=None):
    """Execute an external command in current dir, or in cwd."""
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'UTF-8'
    output = subprocess.check_output(cmd, env=env,
                                     stderr=subprocess.STDOUT,
                                     cwd=cwd)
    return output


class LoginError(Exception):
    """Represents an error when logging in to ReviewBoard."""
    pass


class NotFoundError(Exception):
    """Represents an error when an element cannot be found."""
    pass


class AlreadyExistsError(Exception):
    """Represents an error when an element already exists."""
    pass


def config(section, name, default=None):
    """Return the Mercurial config value with given section and name."""
    try:
        result = extcmd(["hg", "showconfig", section + "." + name]).strip()
    except subprocess.CalledProcessError:
        return default
    if len(result) > 0:
        return result
    else:
        return default


def configbool(section, name, default=False):
    """Return the Mercurial config boolean value section.name."""
    text = config(section, name, str(default))
    if text == "0" or text == "False":
        return False
    else:
        return True


def push_review_hook(node):
    """Run the hook. node is the hex of the first changeset."""
    url = config("reviewboardhook", "reviewboard_url", default="")
    repo_root = extcmd(["hg", "root"]).strip()
    if url == "":
        logging.error("ERROR: {0}/.hg/hgrc should specify".format(repo_root))
        logging.error("the URL to the ReviewBoard server as the")
        logging.error("reviewboard_url setting in the "
                      + "[reviewboardhook] section.")
        return HOOK_FAILED
    try:
        root = get_root(url)
        rbrepo = get_repo(root, repo_root)
    except LoginError as error:
        for line in str(error).split('\n'):
            logging.error(line)
        return HOOK_FAILED
    return push_review_hook_base(root, rbrepo, node)


def get_ticket_url():
    """Return URL root to issue tracker. Warn if not specified in hgrc."""
    ticket_url = config("reviewboardhook", "ticket_url", default="")
    if ticket_url == "":
        repo_root = extcmd(["hg", "root"]).strip()
        logging.warning("WARNING: {0}/.hg/hgrc should specify"
                        .format(repo_root))
        logging.warning("the URL to the bug tracker as the ")
        logging.warning("ticket_url setting in the [reviewboardhook] section.")
        logging.warning("Links to tickets/bugs in the review request summary")
        logging.warning("or description may not work.")
    return ticket_url


def push_review_hook_base(root, rbrepo, node):
    """Run the hook with given API root, Reviewboard repo and changeset."""
    ticket_url = get_ticket_url()
    all_ctx = list_of_incoming(node)
    parent = node + "^1"
    logging.info("{0} changesets received.".format(len(all_ctx)))
    last_approved = -1
    approved_revreq = []
    for i, ctx in enumerate(all_ctx):
        try:
            revreq = find_review_request(root, rbrepo, ctx)
            if approved_by_others(revreq):
                approved_revreq.append(revreq)
                logging.info("Approved review request found for {0}"
                             .format(all_ctx[last_approved+1:i+1]))
                logging.info("URL: {0}".format(revreq.absolute_url))
                last_approved = i
            else:
                logging.info("Pending review request found.")
                if revreq.approved:
                    logging.info("Review request has been approved by you, "
                                 + "but must also be approved by someone else.")
                # Add rest of commits to this review request
                if i < len(all_ctx) - 1:
                    logging.info("Adding new commits to this review request.")
                    update_and_publish(root, ticket_url, all_ctx, revreq,
                                       parent=parent)
                else:
                    logging.info("No new commits since last time.")
                logging.warning("Cannot push until this review request"
                                + " is completed.")
                logging.warning("URL: {0}".format(revreq.absolute_url))
                return HOOK_FAILED
        except NotFoundError:
            pass
        except AlreadyExistsError as error:
            logging.error(str(error))
            return HOOK_FAILED
    approved = False
    remaining_ctx = all_ctx[last_approved+1:]
    if len(remaining_ctx) == 0:
        approved = True
    else:
        allow_merge = configbool("reviewboardhook", "allow_merge",
                                 default=False)
        if allow_merge and all([is_merge(ctx) for ctx in remaining_ctx]):
            logging.info("New commits are merges, "
                         + "which are automatically approved")
            approved = True
    if approved:
        for revreq in approved_revreq:
            logging.info("Closing review request: " + revreq.absolute_url)
            close_request(revreq)
        return HOOK_SUCCESS
    else:
        logging.info("Creating new review request for {0}"
                     .format(all_ctx[last_approved+1:]))
        review_requests = root.get_review_requests(only_fields='',
                                                   only_links='create')
        tip_id = remaining_ctx[-1]
        revreq = review_requests.create(repository=rbrepo,
                                        commit_id=tip_id)
        update_and_publish(root, ticket_url, remaining_ctx, revreq,
                           parent=parent)
        logging.info("The review request must be completed before"
                     " you can push again.")
        logging.info("URL: {0}".format(revreq.absolute_url))
        if last_approved > -1:
            logging.info("If you want to push the already approved changes,")
            logging.info("you can (probably) do this by executing")
            logging.info("'hg push -r {0}'"
                         .format(all_ctx[last_approved]))
        return HOOK_FAILED


def is_merge(commit):
    """Return True if given commit is a merge."""
    res = extcmd(["hg", "log", "-r", "merge() and " + commit]).strip()
    return len(res) > 0


def close_request(rev_req):
    """Close the given review request with a message."""
    message = "Automatically closed by reviewboardhook (invoked by a push)."
    rev_req.update(status="submitted",
                   close_description=message)


def approved_by_others(revreq):
    """Return True if the review request was approved by someone else."""
    if not revreq.approved:
        return False
    revreq_user_id = revreq.get_submitter(only_fields='id', only_links='').id
    reviews = revreq.get_reviews(only_fields='ship_it',
                                 only_links='user')
    for review in reviews:
        review_user = review.get_user(only_fields='id,username',
                                      only_links='')
        user_id = review_user.id
        if review.ship_it:
            logging.debug("Review request {0} has been approved by {1}"
                          .format(revreq.id, review_user.username))
        if review.ship_it and user_id != revreq_user_id:
            return True
    return False


def shorthex(longhex):
    """Return the first 12 characters of longhex."""
    return longhex[:12]


def list_of_incoming(node):
    """Return a list of all changeset hexes after (and including) node.

    Assumes that all incoming changeset have subsequent revision numbers."""
    lines = extcmd(["hg", "log", "-r", node+":",
                    "--template", "{node|short}\n"])
    return lines.split("\n")[:-1]


def find_review_request(root, rbrepo_id, commit_id):
    """Find a review request in the given repo with the given commit ID."""
    fields = 'approved,id,absolute_url'
    links = 'submitter,reviews,update,diffs,draft'

    revreqs = root.get_review_requests(commit_id=commit_id,
                                       repository=rbrepo_id,
                                       status='all',
                                       only_fields=fields,
                                       only_links=links)
    if len(revreqs) > 0:
        return revreqs[0]
    else:
        raise NotFoundError("Review request with commit ID"
                            + "{0} not found".format(commit_id))


def get_root(url):
    """Get API root object."""
    import getpass
    username = getpass.getuser()
    try:
        password = get_password()
        client = RBClient(url, username=username, password=password)
        root = client.get_root()
    except AuthorizationError:
        register_url = url + "account/register/"
        raise LoginError("Dear {0}, ".format(username)
                         + "login to ReviewBoard failed. \n"
                         + "Please verify that you:\n"
                         + "1. Have a ReviewBoard user named " + username
                         + ".\n You can create a user by visiting\n"
                         + register_url + "\n"
                         + "2. Have your ReviewBoard password in\n"
                         + password_filename())
    except APIError as api_error:
        if api_error.http_status == 404:
            raise LoginError("HTTP 404 error. Is the ReviewBoard URL\n"
                             + "{0} correct?".format(url))
        else:
            raise api_error
    return root


def password_filename():
    """Return the name of the user-specific ReviewBoard password file."""
    home = expanduser("~")
    return home + "/.reviewboardpassword"


def get_password():
    """Return password read from current user's password file."""
    pwdfile = password_filename()
    if not isfile(pwdfile):
        raise LoginError("You should have your ReviewBoard password"
                         + " in {0}".format(pwdfile))
    with open(pwdfile) as fileobj:
        password = fileobj.readline().rstrip('\n')
    return password


def admin_email(root):
    """Return admin email."""
    users = root.get_users(q='admin', only_fields='email',
                           only_links='')
    return users[0].email


def get_repo(root, path):
    """Get ID for repository with given file path."""
    repos = root.get_repositories(path=path, only_fields='id',
                                  only_links='')
    if repos.num_items < 1:
        raise LoginError("Could not open ReviewBoard repository for path"
                         + "{0}".format(path)
                         + "Do you have the permissions to access this"
                         + " repository? Ask admin ({0})"
                         .format(admin_email(root))
                         + " to get permissions.")
    return repos[0].id


if __name__ == "__main__":
    import sys
    logging.basicConfig(format='%(levelname)s: %(message)s',
                        level=logging.INFO)
    sys.exit(push_review_hook(node=os.environ['HG_NODE']))
