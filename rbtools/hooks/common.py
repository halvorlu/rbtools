from __future__ import print_function, unicode_literals

import logging
import subprocess
import re

from rbtools.api.client import RBClient
from rbtools.api.errors import APIError, ServerInterfaceError


SUBMITTED = 'submitted'


class HookError(Exception):
    pass


def get_api(server_url, username, password):
    """Returns an RBClient instance and the associated root resource.

    Hooks should use this method to gain access to the API, instead of
    instantianting their own client.
    """
    api_client = RBClient(server_url, username=username, password=password)

    try:
        api_root = api_client.get_root()
    except ServerInterfaceError as e:
        raise HookError('Could not reach the Review Board server at %s: %s'
                        % (server_url, e))
    except APIError as e:
        raise HookError('Unexpected API Error: %s' % e)

    return api_client, api_root


def execute(command):
    """Executes the specified command and returns the stdout output."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    output = process.communicate()[0].strip()

    if process.returncode:
        logging.warning('Failed to execute command: %s', command)
        return None

    return output


def initialize_logging():
    """Sets up a log handler to format log messages.

    Warning, error, and critical messages will show the level name as a prefix,
    followed by the message.
    """
    root = logging.getLogger()

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    handler.setLevel(logging.WARNING)
    root.addHandler(handler)


def get_review_request_id(regex, commit_message):
    """Returns the review request ID referenced in the commit message.

    We assume there is at most one review request associated with each commit.
    If a matching review request cannot be found, we return 0.
    """
    match = regex.search(commit_message)
    return (match and int(match.group('id'))) or 0


def get_review_request(review_request_id, api_root):
    """Returns the review request resource for the given ID."""
    try:
        review_request = api_root.get_review_request(
            review_request_id=review_request_id)
    except APIError as e:
        raise HookError('Error getting review request: %s' % e)

    return review_request


def close_review_request(server_url, username, password, review_request_id,
                         description):
    """Closes the specified review request as submitted."""
    api_client, api_root = get_api(server_url, username, password)
    review_request = get_review_request(review_request_id, api_root)

    if review_request.status == SUBMITTED:
        logging.warning('Review request #%s is already %s.',
                        review_request_id, SUBMITTED)
        return

    if description:
        review_request = review_request.update(status=SUBMITTED,
                                               description=description)
    else:
        review_request = review_request.update(status=SUBMITTED)

    print('Review request #%s is set to %s.' %
          (review_request_id, review_request.status))


def get_review_request_approval(server_url, username, password,
                                review_request_id):
    """Returns the approval information for the given review request."""
    api_client, api_root = get_api(server_url, username, password)
    review_request = get_review_request(review_request_id, api_root)

    return review_request.approved, review_request.approval_failure


TICKET_VERBS = ['close', 'closed', 'closes', 'fix', 'fixes', 'fixed',
                'addresses', 're', 'references', 'refs', 'see',
                'issue', 'bug', 'ticket']

TICKET_TRIGGER = '(?:' + '|'.join(TICKET_VERBS) + r')\s*(?:ticket|bug)?:*\s*'
TICKET_JOIN = r'\s*(?:,|and|, and)\s*'


def linkify_ticket_refs(text, base_url, prefixes=None):
    """Return a text where all ticket references have been linkified.

    prefixes is a list of prefixes allowed before the ticket number.
    For example, prefixes=['app-', ''] would recognize both 'app-1' and '1'
    as bug/ticket number 1. By default, prefixes=[''].
    """
    if prefixes is None:
        prefixes = ['']

    safe_prefixes = [re.escape(prefix) for prefix in prefixes]
    ticket_id = '(#?((?:' + '|'.join(safe_prefixes) + r')\d+))'

    def replace_num_with_link(matchobj):
        """Replace all numbers with links."""
        substr = text[matchobj.start():matchobj.end()]
        escape_url = base_url.replace('\\', '\\\\')
        substr = re.sub(ticket_id, r'[\1](%s\2)' % escape_url, substr)
        return substr

    text = re.sub(TICKET_TRIGGER + ticket_id +
                  ('(?:' + TICKET_JOIN + ticket_id + ')?') * 10,
                  replace_num_with_link, text,
                  flags=re.IGNORECASE)
    return text


def find_ticket_refs(text, prefixes=None):
    """Return list of ticket IDs referenced in given text.

    prefixes is a list of prefixes allowed before the ticket number.
    For example, prefixes=['app-', ''] would recognize both 'app-1' and '1'
    as ticket IDs. By default, prefixes=[''].
    """
    if prefixes is None:
        prefixes = ['']
    ids = set()
    safe_prefixes = [re.escape(prefix) for prefix in prefixes]
    ticket_id = '#?((?:' + '|'.join(safe_prefixes) + r')\d+)'
    matches = re.findall(TICKET_TRIGGER + ticket_id +
                         ('(?:' + TICKET_JOIN + ticket_id + ')?') * 10, text,
                         flags=re.IGNORECASE)
    ids = [submatch for match in matches for submatch in match if submatch]
    return sorted(list(ids))
