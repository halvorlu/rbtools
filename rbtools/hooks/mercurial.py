"""Generic functions for Mercurial hooks."""
from __future__ import unicode_literals

from rbtools.api.client import RBClient
from rbtools.api.errors import AuthorizationError, APIError
from rbtools.clients.mercurial import MercurialClient
from rbtools.hooks.common import linkify_ticket_refs, find_ticket_refs
import logging
import subprocess
import os
import hashlib
from datetime import datetime


HOOK_FAILED = True  # True means error (not equal to zero)
HOOK_SUCCESS = False  # False means OK (equal to zero)
DELIMITER = "\\(reviewboardhook will keep text below this line\\)\n"


def generate_summary(changesets):
    """Generate a summary from a list of changeset ids."""
    summary = extcmd(["hg", "log", "-r", changesets[0], "--template",
                      "{desc}"])
    return summary.decode('utf-8').split("\n")[0]


def generate_description(changesets):
    """Generate a description from a list of changeset ids."""
    header = "{0} changesets:\n".format(len(changesets))
    maintext = extcmd(["hg", "log", "-r", changesets[0] + ":" + changesets[-1],
                       "--template",
                       "{author} ({date|isodate}):\n{desc}\n\n"])
    return header + maintext.decode('utf-8')


def join_descriptions(old_description, new_description):
    """Join two descriptions, keeping any changes after delimiter."""
    delim_index = old_description.find(DELIMITER)
    if delim_index == -1:
        keep = DELIMITER
    else:
        keep = old_description[delim_index:]
    return new_description + keep


def upload_diff(root, changesets, revreq, parent):
    """Upload a diff for the given changesets to given review request."""
    differ = MercurialDiffer(root)
    diff_info = differ.diff(changesets[0] + "^1",
                            changesets[-1], parent)
    parent_diff = diff_info['parent_diff']
    diffs = revreq.get_diffs(only_links='upload_diff', only_fields='')
    if len(parent_diff) > 0:
        diffs.upload_diff(diff_info['diff'],
                          parent_diff=parent_diff,
                          base_commit_id=diff_info['base_commit_id'])
    else:
        diffs.upload_diff(diff_info['diff'])


def date_author_hash(changeset):
    """Return a MD5 hash (base64) of the author/date of given changeset."""
    text = extcmd(["hg", "log", "-r", changeset,
                   "--template", "{author} {date}"])
    return hashlib.md5(text).hexdigest()


def update_and_publish(root, ticket_url, ticket_prefixes,
                       changesets, revreq, parent=None):
    """Update and publish given review request based on changesets.

    parent is the last commit known by the repository before the push."""
    old_description = revreq.description
    new_plain_description = generate_description(changesets)
    linked_description = linkify_ticket_refs(new_plain_description,
                                             ticket_url, ticket_prefixes)
    description = join_descriptions(old_description, linked_description)
    summary = generate_summary(changesets)
    if parent is None:
        parent = changesets[0] + "^1"
    upload_diff(root, changesets, revreq, parent)
    string_refs = [str(x) for x in find_ticket_refs(new_plain_description)]
    bugs_closed = ",".join(string_refs)
    commit_id = date_author_hash(changesets[-1])
    extra_data = {'extra_data.real_commit_id': changesets[-1]}
    revreq.update(**extra_data)
    draft = revreq.get_draft(only_links='update', only_fields='')
    draft = draft.update(
        summary=summary,
        bugs_closed=bugs_closed,
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


def hg_config(section, name, default=None):
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
    text = hg_config(section, name, str(default))
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


def datetime_from_timestamp(timestamp):
    """Return a datetime object from a ReviewBoard timestamp."""
    timestamp_format = "%Y-%m-%dT%H:%M:%SZ"
    return datetime.strptime(timestamp, timestamp_format)


def get_last_diff_time(revreq):
    """Return datetime object of last diff upload to given review request."""
    diffs = revreq.get_diffs(only_fields='timestamp')
    datetimes = [datetime_from_timestamp(diff.timestamp) for diff in diffs]
    return max(datetimes)


def approved_by_others(revreq):
    """Return True if the review request was approved by someone else.

    The approval must have been given after the last diff update."""
    if not revreq.approved:
        return False
    diff_date = get_last_diff_time(revreq)
    revreq_user_id = revreq.get_submitter(only_fields='id', only_links='').id
    reviews = revreq.get_reviews(only_fields='ship_it,timestamp',
                                 only_links='user')
    old_reviews = []
    self_approved = False
    for review in reviews:
        review_user = review.get_user(only_fields='id,username',
                                      only_links='')
        user_id = review_user.id
        if review.ship_it:
            logging.debug("Review request {0} has been approved by {1}"
                          .format(revreq.id, review_user.username))
            review_time = datetime_from_timestamp(review.timestamp)
            if review_time < diff_date:
                old_reviews.append(review_user.username)
            elif user_id != revreq_user_id:
                return True
            if user_id == revreq_user_id:
                self_approved = True
    if len(old_reviews) > 0:
        logging.info("Review request has been approved by "
                     + ", ".join(old_reviews) + ",")
        logging.info("but not after the last diff update.")
    if self_approved:
        logging.info("Review request has been approved by you,")
        logging.info("but must also be approved by someone else.")
    return False


def shorthex(longhex):
    """Return the first 12 characters of longhex."""
    return longhex[:12]


def list_of_incoming(node):
    """Return a list of all changeset hexes after (and including) node.

    Assumes that all incoming changeset have subsequent revision numbers."""
    lines = extcmd(["hg", "log", "-r", node + ":",
                    "--template", "{node|short}\n"])
    return lines.split("\n")[:-1]


def find_review_request(root, rbrepo_id, changeset):
    """Find a review request in the given repo for the given changeset."""
    fields = 'approved,id,absolute_url,commit_id,description,extra_data'
    links = 'submitter,reviews,update,diffs,draft'

    commit_id = date_author_hash(changeset)

    revreqs = root.get_review_requests(commit_id=commit_id,
                                       repository=rbrepo_id,
                                       status='all',
                                       only_fields=fields,
                                       only_links=links)
    if len(revreqs) > 0:
        return revreqs[0]
    else:
        raise NotFoundError("Review request with commit ID" +
                            "{0} not found".format(commit_id))


def find_review_requests(root, rbrepoid, changesets):
    """Return rev. requests that match changesets, and changesets' indices.

    Also returns a list indicating whether the match was exact or not.
    An exact match means that both date, author and commit ID of the changeset
    match the review request."""
    revreqs = []
    indices = []
    exact = []
    for i, changeset in enumerate(changesets):
        try:
            revreq = find_review_request(root, rbrepoid, changeset)
            revreqs.append(revreq)
            indices.append(i)
            if 'real_commit_id' in revreq.extra_data \
               and revreq.extra_data['real_commit_id'] == changeset:
                exact.append(True)
            else:
                exact.append(False)
        except NotFoundError:
            pass
    return revreqs, indices, exact


def find_last_approved(revreqs):
    """Return index of last approved review request in the list, or -1."""
    last_approved = -1
    for i, revreq in enumerate(revreqs):
        if approved_by_others(revreq):
            logging.info("Approved review request found: {0}"
                         .format(revreq.absolute_url))
            last_approved = i
    return last_approved


def get_username(config):
    """Return username from config or guess at the current username."""
    import getpass
    if 'USERNAME' in config:
        username = config['USERNAME']
    else:
        username = getpass.getuser()
        logging.warning("You have not specified any username " +
                        "in ~/.reviewboardrc")
        logging.warning("Assuming '{0}' as username.".format(username))
    return username


def get_password_or_token(config):
    """Read either password (preferred) or API token from config."""
    if 'API_TOKEN' in config:
        return None, config['API_TOKEN']
    elif 'PASSWORD' in config:
        return config['PASSWORD'], None
    else:
        raise LoginError("You need to specify either a password " +
                         "or API token\n" +
                         "for ReviewBoard in your .reviewboardrc file.")


def get_root(config):
    """Get API root object."""
    username = get_username(config)
    password, api_token = get_password_or_token(config)
    if 'REVIEWBOARD_URL' in config:
        url = config['REVIEWBOARD_URL']
    else:
        raise LoginError("You need to specify REVIEWBOARD_URL in the repo's" +
                         " .reviewboardrc file.")
    if 'ENABLE_PROXY' in config:
        enable_proxy = config['ENABLE_PROXY']
    else:
        enable_proxy = True
    try:
        client = RBClient(url, username=username, password=password,
                          api_token=api_token,
                          disable_proxy=not enable_proxy)
        root = client.get_root()
    except AuthorizationError:
        register_url = url + "account/register/"
        raise LoginError("Login to ReviewBoard failed. \n" +
                         "Please verify that you:\n" +
                         "1. Have a ReviewBoard user named " + username +
                         ".\nYou can create a user by visiting\n" +
                         register_url + "\n" +
                         "2. Have either a password or API token in " +
                         "~/.reviewboardrc or the repo's .reviewboardrc.")
    except APIError as api_error:
        if api_error.http_status == 404:
            raise LoginError("HTTP 404 error. Is the ReviewBoard URL\n" +
                             "{0} correct?".format(url))
        else:
            raise api_error
    return root


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
        raise LoginError("Could not open ReviewBoard repository for path\n" +
                         "{0}\n".format(path) +
                         "Do you have the permissions to access this" +
                         " repository?\nAsk admin ({0})"
                         .format(admin_email(root)) +
                         " to get permissions.")
    return repos[0].id
