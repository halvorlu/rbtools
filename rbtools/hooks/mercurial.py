"""Generic functions for Mercurial hooks."""
from __future__ import unicode_literals

from datetime import datetime
import hashlib
import logging
import os
import subprocess

import six

from rbtools.api.client import RBClient
from rbtools.api.errors import AuthorizationError, APIError
from rbtools.clients.mercurial import MercurialClient
from rbtools.hooks.common import linkify_ticket_refs, find_ticket_refs


LOGGER = logging.getLogger('reviewboardhook')
HOOK_FAILED = True  # True means error (not equal to zero)
HOOK_SUCCESS = False  # False means OK (equal to zero)
DELIMITER = '\\(reviewboardhook will keep text below this line\\)\n'


def generate_summary(changesets):
    """Generate a summary from a list of changeset ids."""
    summary = extcmd(['hg', 'log', '-r', changesets[0], '--template',
                      '{desc}'])
    return summary.decode('utf-8').split('\n')[0]


def generate_description(changesets):
    """Generate a description from a list of changeset ids."""
    header = '%d changesets:\n' % len(changesets)
    maintext = extcmd(['hg', 'log', '-r', changesets[0] + ':' + changesets[-1],
                       '--template',
                       '{author} ({date|isodate}):\n{desc}\n\n'])
    return header + maintext.decode('utf-8')


def join_descriptions(old_description, new_description):
    """Join two descriptions, keeping any changes after delimiter."""
    delim_index = old_description.find(DELIMITER)

    if delim_index == -1:
        keep = DELIMITER
    else:
        keep = old_description[delim_index:]

    return new_description + keep


def calculate_diff(root, changesets, parent):
    """Calculate the diff for the given changesets."""
    differ = MercurialDiffer(root)
    return differ.diff(changesets[0] + '^1', changesets[-1], parent)


def upload_diff(diff_info, revreq, diff_hash):
    """Upload diff to review request."""
    parent_diff = diff_info['parent_diff']
    diffs = revreq.get_diffs(only_links='upload_diff', only_fields='')

    if len(parent_diff) > 0:
        diffs.upload_diff(diff_info['diff'],
                          parent_diff=parent_diff,
                          base_commit_id=diff_info['base_commit_id'])
    else:
        diffs.upload_diff(diff_info['diff'])
    extra_data = {'extra_data.diff_hash': diff_hash}
    revreq.update(**extra_data)


def date_author_hash(changeset):
    """Return a MD5 hash (base64) of the author/date of given changeset."""
    text = extcmd(['hg', 'log', '-r', changeset,
                   '--template', '{author} {date}'])
    return hashlib.md5(text).hexdigest()


def update_diff(root, changesets, revreq, parent=None):
    """Return True if the diff had to be (and was) updated."""
    if parent is None:
        parent = changesets[0] + '^1'
    diff_info = calculate_diff(root, changesets, parent)
    diff_hash = calc_diff_hash(diff_info['diff'])
    if 'diff_hash' in revreq.extra_data:
        if diff_hash == revreq.extra_data['diff_hash']:
            return False
    upload_diff(diff_info, revreq, diff_hash)
    return True


def calc_diff_hash(diff):
    """Calculate diff hash that doesn't care about commit IDs."""
    hasher = hashlib.md5()
    for line in diff.split(b'\n'):
        if not line.startswith(b'diff'):
            hasher.update(line)
    return hasher.hexdigest()


def update_draft(root, ticket_url, ticket_prefixes,
                 changesets, revreq, parent=None):
    """Update review request draft based on changesets.

    parent is the last commit known by the repository before the push.
    """
    old_description = revreq.description
    new_plain_description = generate_description(changesets)
    linked_description = linkify_ticket_refs(new_plain_description,
                                             ticket_url, ticket_prefixes)
    description = join_descriptions(old_description, linked_description)
    summary = generate_summary(changesets)

    update_diff(root, changesets, revreq, parent)

    string_refs = [six.text_type(x)
                   for x in find_ticket_refs(new_plain_description)]
    bugs_closed = ','.join(string_refs)
    commit_id = date_author_hash(changesets[-1])
    extra_data = {'extra_data.real_commit_id': changesets[-1]}
    revreq.update(**extra_data)
    branch = extcmd(['hg', 'branch']).strip()
    draft = revreq.get_or_create_draft(only_links='update', only_fields='')
    draft = draft.update(
        summary=summary,
        bugs_closed=bugs_closed,
        description=description,
        description_text_type='markdown',
        branch=branch,
        commit_id=commit_id)


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
        If parent is unspecified, it is assumed to be rev1.
        """
        if parent is None:
            parent = rev1
        revisions = {'base': rev1,
                     'tip': rev2,
                     'parent_base': parent}
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
    """Represents an error when logging in to Review Board."""
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
        result = extcmd(['hg', 'showconfig', section + '.' + name]).strip()
    except subprocess.CalledProcessError:
        return default

    if len(result) > 0:
        return result
    else:
        return default


def configbool(section, name, default=False):
    """Return the Mercurial config boolean value section.name."""
    text = hg_config(section, name, six.text_type(default))
    if text == '0' or text == 'False':
        return False
    else:
        return True


def is_merge(commit):
    """Return True if given commit is a merge."""
    res = extcmd(['hg', 'log', '-r', 'merge() and ' + commit]).strip()
    return len(res) > 0


def close_request(rev_req):
    """Close the given review request with a message."""
    message = 'Automatically closed by reviewboardhook (invoked by a push).'
    rev_req.update(status='submitted', close_description=message)


def datetime_from_timestamp(timestamp):
    """Return a datetime object from a Review Board timestamp."""
    return datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ')


def get_last_diff_time(revreq):
    """Return datetime object of last diff upload to given review request."""
    return max([
        datetime_from_timestamp(diff.timestamp)
        for diff in revreq.get_diffs(only_fields='timestamp')
    ])


def approved_by_others(revreq):
    """Return True if the review request was approved by someone else.

    The approval must have been given after the last diff update.
    """
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
            review_time = datetime_from_timestamp(review.timestamp)
            if review_time < diff_date:
                old_reviews.append(review_user.username)
            elif user_id != revreq_user_id:
                return True
            if user_id == revreq_user_id:
                self_approved = True

    if len(old_reviews) > 0:
        LOGGER.info('Review request %d has been approved by ' +
                    ', '.join(old_reviews) + ',', revreq.id)
        LOGGER.info('but not after the last diff update.')
        LOGGER.debug('Last update: %s', str(diff_date))

    if self_approved:
        LOGGER.info('Review request has been approved by you,')
        LOGGER.info('but must also be approved by someone else.')

    return False


def shorthex(longhex):
    """Return the first 12 characters of longhex."""
    return longhex[:12]


def list_of_incoming(node):
    """Return a list of all changeset hexes after (and including) node.

    Assumes that all incoming changeset have subsequent revision numbers.
    """
    lines = extcmd(['hg', 'log', '-r', node + ':',
                    '--template', '{node|short}\n'])
    return lines.split('\n')[:-1]


def find_review_request(root, rbrepo_id, changeset):
    """Find a review request in the given repo for the given changeset."""
    fields = 'approved,id,absolute_url,commit_id,description,extra_data'
    links = 'submitter,reviews,update,diffs,draft,self'

    commit_id = date_author_hash(changeset)

    revreqs = root.get_review_requests(commit_id=commit_id,
                                       repository=rbrepo_id,
                                       status='all',
                                       show_all_unpublished=True,
                                       only_fields=fields,
                                       only_links=links)
    if len(revreqs) > 0:
        return revreqs[0]
    else:
        raise NotFoundError('Review request with commit ID'
                            '%s not found' % commit_id)


def find_review_requests(root, rbrepoid, changesets):
    """Return rev. requests that match changesets, and changesets' indices."""
    revreqs = []
    indices = []
    for i, changeset in enumerate(changesets):
        try:
            revreq = find_review_request(root, rbrepoid, changeset)
            revreqs.append(revreq)
            indices.append(i)
        except NotFoundError:
            pass
    return revreqs, indices


def exact_match(revreq, changeset):
    """Return True if the review request matches the changeset exactly.

    An exact match means that both date, author and commit ID of the changeset
    match the review request.
    """
    return 'real_commit_id' in revreq.extra_data \
        and revreq.extra_data['real_commit_id'] == changeset


def find_last_approved(revreqs):
    """Return index of last approved review request in the list, or -1."""
    last_approved = -1
    for i, revreq in enumerate(revreqs):
        if approved_by_others(revreq):
            LOGGER.info('Approved review request found: %s',
                        revreq.absolute_url)
            last_approved = i
    return last_approved


def get_username(config):
    """Return username from config or guess at the current username."""
    import getpass
    if 'USERNAME' in config:
        username = config['USERNAME']
    else:
        username = getpass.getuser()
        LOGGER.warning('You have not specified any username '
                       'in ~/.reviewboardrc')
        LOGGER.warning('Assuming %s as username.', username)
    return username


def get_password_or_token(config):
    """Read either password (preferred) or API token from config."""
    if 'API_TOKEN' in config:
        return None, config['API_TOKEN']
    elif 'PASSWORD' in config:
        return config['PASSWORD'], None
    else:
        raise LoginError('You need to specify either a password or API token\n'
                         'for Review Board in your .reviewboardrc file.')


def get_root(config):
    """Get API root object."""
    username = get_username(config)
    password, api_token = get_password_or_token(config)

    if 'REVIEWBOARD_URL' in config:
        url = config['REVIEWBOARD_URL']
    else:
        raise LoginError('You need to specify REVIEWBOARD_URL in the '
                         "repository's .reviewboardrc file.")

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
        register_url = url + 'account/register/'
        raise LoginError('Login to Review Board failed. \n'
                         'Please verify that you:\n'
                         '1. Have a Review Board user named %s'
                         '.\nYou can create a user by visiting\n %s\n'
                         '2. Have either a password or API token in '
                         "~/.reviewboardrc or the repository's .reviewboardrc."
                         % (username, register_url))
    except APIError as api_error:
        if api_error.http_status == 404:
            raise LoginError('HTTP 404 error. Is the Review Board URL\n'
                             '%s correct?' % url)
        else:
            raise api_error

    return root


def get_repo(root, path):
    """Get ID for repository with given file path."""
    repos = root.get_repositories(path=path, only_fields='id',
                                  only_links='')
    if repos.num_items < 1:
        raise LoginError('Could not open Review Board repository '
                         'for path\n%s\n'
                         'Do you have the permissions to access this '
                         'repository?\nAsk the administrator '
                         'to get permissions.' % path)
    return repos[0].id
