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

ticket_url:
The URL for the issue/bug/ticket tracker. Any references to issues/bugs/tickets
in the commit messages are linked to this URL as <ticket_url><ticket_id>

allow_merge:
True/1 if merges are automatically approved, False/0 if not (default).

To enable the hook, add the following line in the [hooks] section:
pretxnchangegroup.rb = /path/to/hook/mercurial-push
"""
from rbtools.hooks.mercurial import *
from rbtools.utils.filesystem import load_config
import logging
import getpass


def push_review_hook(node):
    """Run the hook. node is the hex of the first changeset."""
    config = load_config()
    repo_root = extcmd(["hg", "root"]).strip()
    try:
        root = get_root(config)
        rbrepo = get_repo(root, repo_root)
    except LoginError as error:
        for line in str(error).split('\n'):
            logging.error(line)
        return HOOK_FAILED
    if 'REVIEWBOARD_URL' not in config:
        logging.error("You need to specify REVIEWBOARD_URL in the repo's"
                      + " .reviewboardrc file.")
        return HOOK_FAILED
    else:
        url = config['REVIEWBOARD_URL']
    return push_review_hook_base(root, rbrepo, node, url,
                                 submitter=getpass.getuser())


def get_ticket_url():
    """Return URL root to issue tracker. Warn if not specified in hgrc."""
    ticket_url = config("reviewboardhook", "ticket_url", default="")
    if ticket_url == "":
        repo_root = extcmd(["hg", "root"]).strip()
        logging.warning("{0}/.hg/hgrc should specify"
                        .format(repo_root))
        logging.warning("the URL to the bug tracker as the ")
        logging.warning("ticket_url setting in the [reviewboardhook] section.")
        logging.warning("Links to tickets/bugs in the review request summary")
        logging.warning("or description may not work.")
    return ticket_url


def push_review_hook_base(root, rbrepo, node, url, submitter):
    """Run the hook with given API root, Reviewboard repo and changeset.

    url is the ReviewBoard server URL.
    submitter is the user name of the user that is submitting."""
    ticket_url = get_ticket_url()
    changesets = list_of_incoming(node)
    parent = node + "^1"
    logging.info("{0} changesets received.".format(len(changesets)))
    last_approved = -1
    revreqs = []
    not_approved = changesets
    for i, changeset in enumerate(changesets):
        try:
            revreq = find_review_request(root, rbrepo, changeset)
            if approved_by_others(revreq):
                revreqs.append(revreq)
                logging.info("Approved review request found for {0}"
                             .format(changesets[last_approved + 1:i + 1]))
                logging.info("URL: {0}".format(revreq.absolute_url))
                last_approved = i
                not_approved = changesets[last_approved + 1:]
            else:
                not_known = changesets[i + 1:]
                add_to_pending(revreq, root, ticket_url, not_approved,
                               not_known, parent)
                return HOOK_FAILED
        except NotFoundError:
            pass
    approved = is_approved(not_approved)
    if approved:
        for revreq in revreqs:
            logging.info("Closing review request: " + revreq.absolute_url)
            close_request(revreq)
        return HOOK_SUCCESS
    else:
        try:
            create(root, rbrepo, submitter, url, ticket_url, not_approved,
                   parent)
        except APIError:
            return HOOK_FAILED
        if last_approved > -1:
            logging.info("If you want to push the already approved changes,")
            logging.info("you can (probably) do this by executing")
            logging.info("'hg push -r {0}'"
                         .format(changesets[last_approved]))
        return HOOK_FAILED


def is_approved(changesets):
    """Return True if the list of changesets are approved for pushing.

    Approval means that all changesets are merges, and that merges are allowed,
    or that len(changesets) == 0."""
    if len(changesets) == 0:
        return True
    else:
        allow_merge = configbool("reviewboardhook", "allow_merge",
                                 default=False)
        if allow_merge and all([is_merge(ctx) for ctx in changesets]):
            logging.info("New commits are merges, "
                         + "which are automatically approved")
            return True
    return False


def add_to_pending(revreq, root, ticket_url, changesets, not_known, parent):
    """Add any new changesets (in not_known) to pending review request."""
    logging.info("Pending review request found.")
    if revreq.approved:
        logging.info("Review request has been approved by you, but"
                     + " must also be approved by someone else.")
    # Add rest of commits to this review request
    if len(not_known) > 0:
        logging.info("Adding new commits to this review request.")
        update_and_publish(root, ticket_url, not_approved,
                           revreq, parent=parent)
    else:
        logging.info("No new commits since last time.")
    logging.warning("Cannot push until this review request"
                    + " is completed.")
    logging.warning("URL: {0}".format(revreq.absolute_url))




def create(root, rbrepoid, submitter, url, ticket_url,
           changesets, parent):
    """Create a new review request for the given changesets."""
    logging.info("Creating new review request for {0}"
                 .format(changesets))
    review_requests = root.get_review_requests(only_fields='',
                                               only_links='create')
    tip_id = changesets[-1]
    try:
        revreq = review_requests.create(commit_id=tip_id,
                                        repository=rbrepoid,
                                        submit_as=submitter)
    except APIError as api_error:
        if api_error.error_code == 208:
            register_url = url + "account/register/"
            logging.error("Could not create review request.")
            logging.error("Make sure you have created a user named '"
                          + submitter + "'.")
            logging.error("Go to " + register_url + " to create a user.")
        raise api_error
    update_and_publish(root, ticket_url, changesets,
                       revreq, parent=parent)
    logging.info("The review request must be completed before"
                 " you can push again.")
    logging.info("URL: {0}".format(revreq.absolute_url))


if __name__ == "__main__":
    import sys
    logging.basicConfig(format='%(levelname)s: %(message)s',
                        level=logging.INFO)
    sys.exit(push_review_hook(node=os.environ['HG_NODE']))
