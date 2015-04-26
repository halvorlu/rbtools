#!/usr/bin/env python2
# coding: utf-8
"""A Mercurial hook to post to Review Board on push to a central server.

The hook was designed to make posting to Review Board easy for new or
inexperienced users. It allows user to post to Review Board by using the
ordinary 'hg push', without any need to learn or install RBTools locally.

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
The hook uses a date/author hash to recognize changesets, so amended/rebased
changesets are recognized properly.

The hook submits review requests using the username of the current user, but
can be configured to use a special user for logging in, which submits on
behalf of the actual user. This can be configured through the .reviewboardrc
file.

Configuration:
The hook is configured through:
1. The .reviewboardrc file of the repository (and user)
2. A section in the central repository's hgrc file in the [reviewboardhook]
   section.

In <repo>/.reviewboardrc, the following settings are relevant:
REVIEWBOARD_URL: The URL of the Review Board server
API_TOKEN: An API token to use for logging in, which allows submitting
           review requests on behalf of other users.

In <repo>/.reviewboardrc or ~/.reviewboardrc:
USERNAME: The username to use for logging into the server
PASSWORD: The password to use for logging into the server

In <repo>/.hg/hgrc, the following settings are relevant:
ticket_url:
The URL for the issue/bug/ticket tracker. Any references to issues/bugs/tickets
in the commit messages are linked to this URL as <ticket_url><ticket_id>

ticket_id_prefixes:
Comma-separated list of prefixes that are allowed in ticket IDs.
Example: 'ticket-prefixes = app-, prog-' will cause both 'fixes app-1',
'fixes prog-2' and 'fixes 3' to be recognized as references to tickets.

allow_merge:
True/1 if merges should be approved automatically, False/0 if not (default).

publish:
True/1 if review request drafts should be published by the hook (default).

To enable the hook, add the following line in the [hooks] section:
pretxnchangegroup.rb = /path/to/hook/mercurial_push.py
"""
from __future__ import unicode_literals

import getpass
import logging

import six

import rbtools.hooks.mercurial as hghook
from rbtools.api.errors import APIError
from rbtools.utils.filesystem import load_config


LOGGER = logging.getLogger('reviewboardhook')
CONFIG_SECTION = 'reviewboardhook'


def push_review_hook(node):
    """Run the hook. node is the hex of the first changeset."""
    config = load_config()
    if 'REPOSITORY' not in config:
        LOGGER.error('You need to specify REPOSITORY in the '
                     "repository's .reviewboardrc file.")
        return hghook.HOOK_FAILED

    try:
        root = hghook.get_root(config)
        rbrepo = hghook.get_repo(root, config['REPOSITORY'])
    except hghook.LoginError as error:
        for line in six.text_type(error).split('\n'):
            LOGGER.error(line)
        return hghook.HOOK_FAILED

    if 'REVIEWBOARD_URL' not in config:
        LOGGER.error('You need to specify REVIEWBOARD_URL in the '
                     "repository's .reviewboardrc file.")
        return hghook.HOOK_FAILED

    url = config['REVIEWBOARD_URL']

    return push_review_hook_base(root, rbrepo, node, url,
                                 submitter=getpass.getuser())


def get_ticket_url():
    """Return URL root to issue tracker. Warn if not specified in hgrc."""
    ticket_url = hghook.hg_config(CONFIG_SECTION, 'ticket_url', default='')
    if not ticket_url:
        repo_root = hghook.extcmd(['hg', 'root']).strip()
        LOGGER.warning('%s/.hg/hgrc should specify', repo_root)
        LOGGER.warning('the URL to the bug tracker as the ')
        LOGGER.warning('ticket_url setting in the [' +
                       CONFIG_SECTION + '] section.')
        LOGGER.warning('Links to tickets/bugs in the review request summary')
        LOGGER.warning('or description may not work.')

    return ticket_url


def get_ticket_prefixes():
    """Return a list of allowed prefixes in ticket IDs."""
    prefixes = hghook.hg_config(CONFIG_SECTION, 'ticket_id_prefixes',
                                default=None)
    if prefixes is None:
        return ['']
    else:
        prefixes = [x.strip() for x in prefixes.split(',')]
        prefixes.append('')
        return prefixes


def push_review_hook_base(root, rbrepo, node, url, submitter):
    """Run the hook with given API root, Review Board repo and changeset.

    node is the commit ID of the first changeset in the push.
    url is the Review Board server URL.
    submitter is the user name of the user that is submitting.
    """
    ticket_url = get_ticket_url()
    ticket_prefixes = get_ticket_prefixes()
    changesets = hghook.list_of_incoming(node)
    parent = node + '^1'
    LOGGER.info('%d changesets received.', len(changesets))
    revreqs, indices = hghook.find_review_requests(root, rbrepo, changesets)
    LOGGER.info('%d matching review request found.', len(revreqs))

    prev_index = 0
    for revreq, index in zip(revreqs, indices):
        revreq_changesets = changesets[prev_index:index + 1]
        changeset = changesets[index]
        if not hghook.exact_match(revreq, changeset):
            update_and_publish(root, ticket_url, ticket_prefixes,
                               revreq_changesets, revreq, parent=parent)

        prev_index = index + 1

    new_changesets = changesets[prev_index:]
    approvals = [hghook.approved_by_others(revreq) for revreq in revreqs]

    if len(revreqs) > 0:
        last_approved = approvals[-1]
    else:
        last_approved = True

    if last_approved and len(new_changesets) > 0 and\
       not is_approved(new_changesets):
        LOGGER.info('Creating review request for new changesets.')
        revreq = create(root, rbrepo, submitter, url, new_changesets[-1])
        update_and_publish(root, ticket_url, ticket_prefixes,
                           new_changesets, revreq, parent)
        approvals.append(False)
    else:
        LOGGER.info('Pending review request %d found.', revreq.id)
        if len(new_changesets) > 0:
            LOGGER.info('Adding new changesets to this review request.')

        if len(revreqs) > 1:
            revreq_changesets = changesets[indices[-2] + 1:]
        else:
            revreq_changesets = changesets

        update_and_publish(root, ticket_url, ticket_prefixes,
                           revreq_changesets, revreq, parent)
        approvals[-1] = approvals[-1] and is_approved(new_changesets)

    if all(approvals):
        for revreq in revreqs:
            LOGGER.info('Closing review request: ' + revreq.absolute_url)
            hghook.close_request(revreq)
        return hghook.HOOK_SUCCESS
    elif any(approvals):
        last_approved = approvals.index(False) - 1
        if last_approved > -1:
            LOGGER.info('If you want to push the already approved changes,')
            LOGGER.info('you can (probably) do this by executing')
            LOGGER.info('hg push -r %s', changesets[last_approved])

    return hghook.HOOK_FAILED


def update_and_publish(root, ticket_url, ticket_prefixes,
                       changesets, revreq, parent):
    """Update review request draft and publish if config says so."""
    hghook.update_draft(root, ticket_url, ticket_prefixes,
                        changesets, revreq, parent)
    publish_maybe(revreq)


def publish_maybe(revreq):
    """Publish the review request's draft if config says so."""
    publish_draft = hghook.configbool('reviewboardhook', 'publish',
                                      default=True)
    if publish_draft:
        revreq = revreq.get_self()  # Update request to get draft
        try:
            draft = revreq.get_draft(only_links='update',
                                     only_fields='')
        except APIError as api_error:
            # Error code 100 means draft does not exist,
            # i.e. it is already published
            if api_error.error_code != 100:
                raise
        try:
            draft.update(public=True)
        except APIError as api_error:
            # Error code 211 means draft does not have changes
            if api_error.error_code != 211:
                raise
    else:
        LOGGER.info('The review request has not been published yet.')
        LOGGER.info('You must publish it manually.')


def is_approved(changesets):
    """Return True if the list of changesets are approved for pushing.

    Approval means that all changesets are merges, and that merges are allowed,
    or that len(changesets) == 0.
    """
    if len(changesets) == 0:
        return True
    else:
        allow_merge = hghook.configbool('reviewboardhook', 'allow_merge',
                                        default=False)
        if allow_merge and all(hghook.is_merge(ctx) for ctx in changesets):
            LOGGER.info('New commits are merges, '
                        'which are automatically approved')
            return True

    return False


def create(root, rbrepoid, submitter, url, changeset):
    """Return a new review request for the given changesets."""
    review_requests = root.get_review_requests(only_fields='',
                                               only_links='create')
    commit_id = hghook.date_author_hash(changeset)

    try:
        revreq = review_requests.create(commit_id=commit_id,
                                        repository=rbrepoid,
                                        submit_as=submitter)
    except APIError as api_error:
        if api_error.error_code == 208:
            register_url = url + 'account/register/'
            LOGGER.error('Could not create review request.')
            LOGGER.error('Make sure you have created a user named %s.',
                         submitter)
            LOGGER.error('Go to %s to create a user.', register_url)
        raise api_error

    LOGGER.info('The review request must be completed before'
                ' you can push again.')
    LOGGER.info('URL: %s', revreq.absolute_url)
    return revreq


if __name__ == '__main__':
    import sys
    import os
    logging.basicConfig(format='%(levelname)s: %(message)s',
                        level=logging.INFO)
    sys.exit(push_review_hook(node=os.environ['HG_NODE']))
