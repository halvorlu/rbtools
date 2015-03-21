"""Generic functions for Mercurial hooks."""
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
        logging.warning("Could not read the password file " + pwdfile)
        return ""
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
        raise LoginError("Could not open ReviewBoard repository for path\n"
                         + "{0}\n".format(path)
                         + "Do you have the permissions to access this"
                         + " repository?\nAsk admin ({0})"
                         .format(admin_email(root))
                         + " to get permissions.")
    return repos[0].id
