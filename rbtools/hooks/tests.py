"""Tests for rbtools.hooks."""

from rbtools.utils.testbase import RBTestBase
from rbtools.hooks.common import find_ticket_refs, linkify_ticket_refs
from rbtools.hooks.mercurial import join_descriptions, DELIMITER


class CommonTest(RBTestBase):
    """Tests for rbtools.hooks.common."""

    def test_ticket_refs(self):
        """Testing that ticket refs are interpreted as IDs correctly."""
        self.assertEqual([10], find_ticket_refs("fixes #10"))
        self.assertEqual([10, 11, 12],
                         find_ticket_refs("fixes #10, and #11, and 12"))
        self.assertEqual([10], find_ticket_refs("see ticket: #10"))
        self.assertEqual([10], find_ticket_refs("addresses #10"))
        self.assertEqual([1, 2],
                         find_ticket_refs("fixes #1. addresses #2"))
        self.assertEqual([10], find_ticket_refs("fixes bug 10"))
        self.assertEqual([2, 10, 11],
                         find_ticket_refs("see issue: 10,11,2"))
        self.assertEqual([1, 2, 3, 4, 5],
                         find_ticket_refs("fixes 1, 2, 3. " +
                                          "Addresses 3, 4, 5."))

    def test_linkify_refs(self):
        """Testing that references to tickets are linkified."""
        url = "a/"
        self.assertEqual("fixes [#10](a/10)",
                         linkify_ticket_refs("fixes #10", url))
        self.assertEqual("fixes [#10](a/10), [#11](a/11) and [#12](a/12)",
                         linkify_ticket_refs("fixes #10, #11 and #12", url))


class MercurialTest(RBTestBase):
    """Tests for rbtools.hooks.mercurial."""

    def test_join_descriptions(self):
        """Testing that description changes are kept when updating request."""
        user_text = "blabla"
        old = "1 changesets:\nblabla\n" + DELIMITER + user_text
        new = "2 changesets:\nblabla\nfoobar\n"
        joined = join_descriptions(old, new)
        self.assertEqual(new + DELIMITER + user_text, joined)
