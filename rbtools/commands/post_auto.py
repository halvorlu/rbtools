from __future__ import print_function, unicode_literals


from rbtools.commands.post import Post
import logging
import os
import sys

from rbtools.commands import CommandError
from rbtools.utils.review_request import (get_draft_or_current_value,
                                          get_revisions,
                                          guess_existing_review_request_id)


class PostAuto(Post):
    """Create and update review requests automatically."""
    name = 'post-auto'
    author = 'The Review Board Project'
    description = 'Uploads diffs to create and update ' + \
                  'review requests automatically.'
    args = '[revisions]'

    def main(self, *args):
        """Create and update review requests."""
        # The 'args' tuple must be made into a list for some of the
        # SCM Clients code. The way arguments were structured in
        # post-review meant this was a list, and certain parts of
        # the code base try and concatenate args to the end of
        # other lists. Until the client code is restructured and
        # cleaned up we will satisfy the assumption here.
        self.cmd_args = list(args)

        self.post_process_options()
        origcwd = os.path.abspath(os.getcwd())
        repository_info, self.tool = self.initialize_scm_tool(
            client_name=self.options.repository_type)
        server_url = self.get_server_url(repository_info, self.tool)
        api_client, api_root = self.get_api(server_url)
        self.setup_tool(self.tool, api_root=api_root)

        if (self.options.exclude_patterns and
            not self.tool.supports_diff_exclude_patterns):

            raise CommandError(
                'The %s backend does not support excluding files via the '
                '-X/--exclude commandline options or the EXCLUDE_PATTERNS '
                '.reviewboardrc option.' % self.tool.name)

        # Check if repository info on reviewboard server match local ones.
        repository_info = repository_info.find_server_repository_info(api_root)

        if self.options.diff_filename:
            self.revisions = None
            parent_diff = None
            base_commit_id = None
            commit_id = None

            if self.options.diff_filename == '-':
                if hasattr(sys.stdin, 'buffer'):
                    # Make sure we get bytes on Python 3.x
                    diff = sys.stdin.buffer.read()
                else:
                    diff = sys.stdin.read()
            else:
                try:
                    diff_path = os.path.join(origcwd,
                                             self.options.diff_filename)
                    with open(diff_path, 'rb') as fp:
                        diff = fp.read()
                except IOError as e:
                    raise CommandError('Unable to open diff filename: %s' % e)
        else:
            rev_list = self.cmd_args[-1].split(':')
            self.revisions = {'base': rev_list[1], 'tip': rev_list[2],
                              'parent_base': rev_list[0]}
            # Generate a diff against the revisions or arguments, filtering
            # by the requested files if provided.
            diff_info = self.tool.diff(
                revisions=self.revisions,
                include_files=self.options.include_files or [],
                exclude_patterns=self.options.exclude_patterns or [])

            diff = diff_info['diff']
            parent_diff = diff_info.get('parent_diff')
            base_commit_id = diff_info.get('base_commit_id')
            commit_id = diff_info.get('commit_id')

        repository = (
            self.options.repository_name or
            self.options.repository_url or
            self.get_repository_path(repository_info, api_root))

        base_dir = self.options.basedir or repository_info.base_path

        if len(diff) == 0:
            raise CommandError("There don't seem to be any diffs!")

        if repository_info.supports_changesets and 'changenum' in diff_info:
            changenum = diff_info['changenum']
            commit_id = changenum
        else:
            changenum = None

        if not self.options.diff_filename:
            # If the user has requested to guess the summary or description,
            # get the commit message and override the summary and description
            # options.
            self.check_guess_fields()

        if self.options.update and self.revisions:
            self.options.rid = guess_existing_review_request_id(
                repository_info, self.options.repository_name, api_root,
                api_client, self.tool, self.revisions,
                guess_summary=False, guess_description=False,
                is_fuzzy_match_func=self._ask_review_request_match)

            if not self.options.rid:
                raise CommandError('Could not determine the existing review '
                                   'request to update.')

        # If only certain files within a commit are being submitted for review,
        # do not include the commit id. This prevents conflicts if mutliple
        # files from the same commit are posted for review separately.
        if self.options.include_files:
            commit_id = None

        request_id, review_url = self.post_request(
            repository_info,
            repository,
            server_url,
            api_root,
            self.options.rid,
            changenum=changenum,
            diff_content=diff,
            parent_diff_content=parent_diff,
            commit_id=commit_id,
            base_commit_id=base_commit_id,
            submit_as=self.options.submit_as,
            base_dir=base_dir)

        diff_review_url = review_url + 'diff/'

        print('Review request #%s posted.' % request_id)
        print()
        print(review_url)
        print(diff_review_url)

        # Load the review up in the browser if requested to.
        if self.options.open_browser:
            try:
                import webbrowser
                if 'open_new_tab' in dir(webbrowser):
                    # open_new_tab is only in python 2.5+
                    webbrowser.open_new_tab(review_url)
                elif 'open_new' in dir(webbrowser):
                    webbrowser.open_new(review_url)
                else:
                    os.system('start %s' % review_url)
            except:
                logging.error('Error opening review URL: %s' % review_url)
