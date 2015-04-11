"""Tests for reviewboardhook."""
# coding: utf-8
import rbtools.hooks.mercurial as rbh
from mercurial_push import push_review_hook_base
import os
from StringIO import StringIO
import logging
from rbtools.api.client import RBClient
from rbtools.utils.filesystem import load_config


TEST_SERVER = "http://localhost:8090/"
TEST_USER = "testuser"
TEST_PASS = "password"
TEST_REPO_NAME = "unittestrepo"
HOOK_PATH = os.getcwd()
TEST_REPO_PATH = HOOK_PATH + "/" + TEST_REPO_NAME
CLIENT_REPO_NAME = "clientrepo"
CLIENT_REPO_PATH = HOOK_PATH + "/" + CLIENT_REPO_NAME


def extcmd(command, cwd=None):
    """Split command string at spaces and pass to subprocess."""
    return rbh.extcmd(command.split(" "), cwd)


def clean_repos():
    """Create new repos and enter the client repo."""
    extcmd("mkdir {0}".format(TEST_REPO_PATH))
    os.chdir(TEST_REPO_PATH)
    extcmd("hg init")
    write_to_file("tmp.txt", "blø")
    extcmd("hg add tmp.txt")
    write_to_file(".reviewboardrc",
                  "REVIEWBOARD_URL = \"http://localhost:8090\"\n"
                  + "USERNAME = \"" + TEST_USER + "\"\n"
                  + "PASSWORD = \"" + TEST_PASS + "\"\n")
    extcmd("hg add .reviewboardrc")
    rbh.extcmd(["hg", "commit", "-m", "Initial commit"])
    write_to_file(".hg/hgrc", "[reviewboardhook]\n"
                  + "ticket_url = http://none/\n")
    extcmd("hg clone {0} {1}".format(TEST_REPO_PATH, CLIENT_REPO_PATH))
    os.chdir(CLIENT_REPO_PATH)


def delete_repos():
    """Delete repos."""
    extcmd("rm -rf {0}".format(CLIENT_REPO_PATH))
    extcmd("rm -rf {0}".format(TEST_REPO_PATH))


def write_to_file(filename, text):
    """Open filename and write given text."""
    with open(filename, 'w') as fileobj:
        fileobj.write(text)


class FakeRBClient(object):
    def __init__(self, url, username=None, password=None, api_token=None):
        pass

    def get_root(self):
        return FakeRoot(capabilities=None)


class FakeRoot(object):
    def __init__(self, capabilities):
        self.capabilities = capabilities
        self.users = {}

    def get_repositories(self, **kwargs):
        return []

    def get_users(self, q, **kwargs):
        return self.users[q]


class FakeRepoList(object):
    def __init__(self):
        self.num_items = 0

class FakeRepo(object):
    def __init__(self):
        pass


def get_root():
    client = RBClient(TEST_SERVER, username=TEST_USER, password=TEST_PASS)
    return client.get_root()


def get_admin_root():
    client = RBClient(TEST_SERVER, username="admin",
                      password="admin")
    return client.get_root()


def get_repo_id(root):
    return root.get_repositories(name=TEST_REPO_NAME,
                                 only_fields='id',
                                 only_links='')[0].id



differ = None
config = None


def setup_module(module):
    delete_repos()
    root = get_root()
    module.differ = rbh.MercurialDiffer(root)
    module.config = load_config()
    clean_repos()


def delete_revreqs(name):
    adroot = get_admin_root()
    rbrepos = adroot.get_repositories(name=name)
    if rbrepos.num_items > 0:
        revreqs = adroot.get_review_requests(repository=rbrepos[0].id)
        for revreq in revreqs:
            revreq.delete()


def teardown_module(module):
    delete_repos()
    delete_revreqs(TEST_REPO_NAME)
    os.chdir(HOOK_PATH)


class LogCapturer(object):
    """A class to temporarily capture logging output."""
    def __enter__(self):
        logbuffer = StringIO()
        self.handler = logging.StreamHandler(logbuffer)
        logger = logging.getLogger()
        logger.addHandler(self.handler)
        return logbuffer

    def __exit__(self, type, value, traceback):
        rootLogger = logging.getLogger()
        rootLogger.removeHandler(self.handler)


def test_hgdiff():
    """Test that hgdiff generates diff that can be imported."""
    os.chdir(CLIENT_REPO_PATH)
    write_to_file("tmp.txt", "blæ2")
    rbh.extcmd(['hg', 'commit', '-m', 'ÆØÅæøå', '-u', 'User æøå'])
    hexid = extcmd("hg id -i").strip()
    diff = differ.diff(hexid+"^1", hexid)
    assert len(diff['diff']) > 0
    write_to_file("diff", diff['diff'].encode('utf-8'))
    extcmd("hg up tip^1")
    extcmd("hg import diff -m applydiff")
    hexid2 = extcmd("hg id -i").strip()
    diff2 = differ.diff(hexid, hexid2)
    assert len(diff2['diff']) == 0


def test_hgdiff_empty():
    """Test that hgdiff handles empty files."""
    os.chdir(CLIENT_REPO_PATH)
    write_to_file("tmp3.txt", "")
    extcmd("hg add tmp3.txt")
    extcmd("hg commit -m addempty -u æøå")
    hexid = extcmd("hg id -i").strip()
    diff = differ.diff(hexid+"^1", hexid)
    assert len(diff['diff']) > 0


def test_hgdiff_delete():
    """Test that hgdiff handles deleted empty files."""
    os.chdir(CLIENT_REPO_PATH)
    write_to_file("tmp4.txt", "")
    extcmd("hg add tmp4.txt")
    extcmd("hg commit -m addempty -u æøå")
    extcmd("hg rm tmp4.txt")
    extcmd("hg commit -m removeempty -u æøå")
    hexid = extcmd("hg id -i").strip()
    diff = differ.diff(hexid+"^1", hexid)
    assert len(diff['diff']) > 0


def test_get_repo():
    """Test that get_repo returns existing repos."""
    root = get_root()
    os.chdir(CLIENT_REPO_PATH)
    repo_id = rbh.get_repo(root, TEST_REPO_PATH)
    assert repo_id >= 0
    try:
        repo_id2 = rbh.get_repo(root, "nonexistingrepo")
        assert False
    except:
        pass


def test_update_draft():
    """Test that review request as updated and published properly."""
    root = get_root()
    from mercurial import hg, ui
    os.chdir(CLIENT_REPO_PATH)
    ticketurl = ""
    ticket_prefixes = [""]
    extcmd("hg push -r tip")  # Push to main repo to be up-to-date
    write_to_file("tmp5.txt", "hællæ")
    extcmd("hg add tmp5.txt")
    extcmd("hg commit -m tmp5ædd")
    repo = hg.repository(ui.ui(), '.')
    all_ctx = [rbh.shorthex(repo['tip'].hex())]
    rbrepoid = get_repo_id(root)
    review_requests = root.get_review_requests(only_fields='',
                                               only_links='create')
    commit_id = rbh.date_author_hash(repo['tip'].hex())
    revreq = review_requests.create(repository=rbrepoid,
                                    commit_id=commit_id)
    rbh.update_draft(root, ticketurl, ticket_prefixes, all_ctx, revreq)
    revreq.get_draft().update(public=True)
    # Update revreq to reflect the changes
    revreq = root.get_review_requests(id=revreq.id)[0]
    assert revreq.summary == u"tmp5ædd"
    assert revreq.public
    assert revreq.status == 'pending'
    assert revreq.commit_id == commit_id
    # Now make another commit and make sure review request is updated
    write_to_file("tmp5.txt", "hållå")
    extcmd("hg commit -m tmp5hållå")
    repo = hg.repository(ui.ui(), '.')  # Update repo object from file system
    all_ctx = [rbh.shorthex(repo['-2'].hex()), rbh.shorthex(repo['tip'].hex())]
    rbh.update_draft(root, ticketurl, ticket_prefixes, all_ctx, revreq)
    revreq.get_draft().update(public=True)
    # Update revreq to reflect the changes
    revreq = root.get_review_requests(id=revreq.id)[0]
    assert revreq.summary == u"tmp5ædd"
    assert revreq.public
    assert revreq.status == 'pending'
    assert revreq.commit_id == rbh.date_author_hash(repo['tip'].hex())
    assert u"tmp5hållå" in revreq.description
    assert u"tmp5ædd" in revreq.description


def test_hook_base():
    root = get_root()
    from mercurial import hg, ui
    os.chdir(TEST_REPO_PATH)
    write_to_file("tmp6.txt", "høæå")
    extcmd("hg add tmp6.txt")
    extcmd("hg commit -m tmp6")
    write_to_file("tmp7.txt", "høæå")
    extcmd("hg add tmp7.txt")
    extcmd("hg commit -m tmp7")
    repo = hg.repository(ui.ui(), '.')
    firsthex = rbh.shorthex(repo['-2'].hex())
    rbrepo = get_repo_id(root)
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    lasthex = rbh.date_author_hash(repo['tip'].hex())
    revreq = root.get_review_requests(commit_id=lasthex,
                                      repository=rbrepo)[0]
    assert revreq.summary == "tmp6"
    assert "tmp7" in revreq.description
    # Create a self review, this should not let the hook succeed
    approve_revreq(root, rbrepo, lasthex)
    with LogCapturer() as logbuffer:
        assert push_review_hook_base(root, rbrepo, firsthex,
                                     TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
        buffercontents = logbuffer.getvalue()
        assert "has been approved by you" in buffercontents
        assert "must also be approved by someone else" in buffercontents
    # Create a review by someone else, should let the hook succeed
    root = get_admin_root()
    approve_revreq(root, rbrepo, lasthex)
    root = get_root()
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_SUCCESS


def test_push_review_hook_base_parent_diff():
    root = get_root()
    from mercurial import hg, ui
    os.chdir(CLIENT_REPO_PATH)
    write_to_file("tmp8.txt", "høæå")
    extcmd("hg add tmp8.txt")
    extcmd("hg commit -m tmp8")
    repo = hg.repository(ui.ui(), '.')
    firsthex = rbh.shorthex(repo['tip'].hex())
    rbrepo = get_repo_id(root)
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    # Create a review by someone else
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(firsthex))
    root = get_root()
    # Create another commit to check if parent diffs work properly
    write_to_file("tmp8.txt", "blæblåblø")
    extcmd("hg commit -m tmp8again")
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED


def test_push_review_hook_base_merge():
    """Test that hook handles merges."""
    from mercurial import hg, ui
    os.chdir(TEST_REPO_PATH)
    write_to_file("tmp6.txt", "hæææ")
    extcmd("hg add tmp6.txt")
    extcmd("hg commit -m tmp6")
    firsthex = extcmd("hg id -i").strip()
    extcmd("hg up -r -2")
    write_to_file("tmp7.txt", "hæææ")
    extcmd("hg add tmp7.txt")
    extcmd("hg commit -m tmp7")
    extcmd("hg merge {0}".format(firsthex))
    extcmd("hg commit -m merge")
    repo = hg.repository(ui.ui(), '.')
    root = get_root()
    rbrepo = get_repo_id(root)
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    lasthex = rbh.date_author_hash(repo['tip'].hex())
    revreq = root.get_review_requests(commit_id=lasthex,
                                      repository=rbrepo)[0]
    assert revreq.summary == "tmp6"
    assert "tmp7" in revreq.description
    assert "tmp6" in revreq.description
    assert "merge" in revreq.description
    assert revreq.commit_id == lasthex


def test_allow_merge():
    """Test that hook allows merges when TEST_SERVER, TEST_USER says so."""
    os.chdir(TEST_REPO_PATH)
    write_to_file("tmp13.txt", "hæææ")
    extcmd("hg add tmp13.txt")
    extcmd("hg commit -m tmp13")
    firsthex = extcmd("hg id -i").strip()
    extcmd("hg up -r -2")
    write_to_file("tmp14.txt", "hæææ")
    extcmd("hg add tmp14.txt")
    extcmd("hg commit -m tmp14")
    secondhex = extcmd("hg id -i").strip()
    root = get_root()
    rbrepo = get_repo_id(root)
    assert push_review_hook_base(root, rbrepo, secondhex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(secondhex))
    root = get_root()
    # Hijack configbool to return True for reviewboardhook.allow_merge
    oldconfigbool = rbh.configbool
    def configbooltrue(x, y, default=False):
        return True
    rbh.configbool = configbooltrue
    extcmd("hg merge {0}".format(firsthex))
    extcmd("hg commit -m merge")
    assert push_review_hook_base(root, rbrepo, secondhex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_SUCCESS
    rbh.configbool = oldconfigbool
    commit_id = rbh.date_author_hash(secondhex)
    revreq = root.get_review_requests(commit_id=commit_id,
                                      repository=rbrepo,
                                      status='all')[0]
    assert revreq.summary == "tmp14"
    assert revreq.approved
    assert revreq.status == 'submitted'


def approve_revreq(root, rbrepoid, commit_id):
    """Approve review request with given commit id."""
    revreq = root.get_review_requests(commit_id=commit_id,
                                      repository=rbrepoid)[0]
    revs = revreq.get_reviews(only_links='create', only_fields='')
    review = revs.create()
    review.update(ship_it=True, public=True, body_top="I like this!")


def test_push_review_hook_base_branches():
    """Test that hook handles branches."""
    os.chdir(CLIENT_REPO_PATH)
    write_to_file("tmp9.txt", "hæææ")
    extcmd("hg add tmp9.txt")
    extcmd("hg branch newbranch")
    extcmd("hg commit -m tmp9")
    firsthex = extcmd("hg id -i").strip()
    root = get_root()
    rbrepo = get_repo_id(root)
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(firsthex))
    root = get_root()
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_SUCCESS
    extcmd("hg push -f -r newbranch")
    extcmd("hg up default")
    write_to_file("tmp10.txt", "hæææ")
    extcmd("hg add tmp10.txt")
    extcmd("hg commit -m tmp10")
    firsthex = extcmd("hg id -i").strip()
    assert push_review_hook_base(root, rbrepo, firsthex, TEST_SERVER,
                                 TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(firsthex))
    root = get_root()
    assert push_review_hook_base(root, rbrepo, firsthex, TEST_SERVER,
                                 TEST_USER) == rbh.HOOK_SUCCESS
    extcmd("hg push -r {0}".format(firsthex))
    extcmd("hg merge newbranch")
    extcmd("hg commit -m Merge")
    firsthex = extcmd("hg id -i").strip()
    assert rbh.is_merge(firsthex)
    assert push_review_hook_base(root, rbrepo, firsthex, TEST_SERVER,
                                 TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(firsthex))
    root = get_root()
    assert push_review_hook_base(root, rbrepo, firsthex, TEST_SERVER,
                                 TEST_USER) == rbh.HOOK_SUCCESS


def test_amend():
    """Test that hook handles amended changesets."""
    os.chdir(TEST_REPO_PATH)
    write_to_file("tmp20.txt", "hæææ")
    extcmd("hg add tmp20.txt")
    extcmd("hg commit -m tmp20")
    firsthex = extcmd("hg id -i").strip()
    root = get_root()
    rbrepo = get_repo_id(root)
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(firsthex))
    root = get_root()
    write_to_file("tmp20.txt", "høøø")
    extcmd("hg commit --amend -m tmp20-2")
    firsthex = extcmd("hg id -i").strip()
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(firsthex))
    root = get_root()
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_SUCCESS

def test_rebase():
    """Test that hook handles rebased changesets."""
    os.chdir(TEST_REPO_PATH)
    write_to_file("tmp21.txt", "hæææ")
    extcmd("hg add tmp21.txt")
    extcmd("hg commit -m tmp21-1")
    firsthex = extcmd("hg id -i").strip()
    root = get_root()
    rbrepo = get_repo_id(root)
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    write_to_file("tmp21.txt", "blæææ")
    extcmd("hg commit -m tmp21-2")
    secondhex = extcmd("hg id -i").strip()
    extcmd("hg up -r -2")
    write_to_file("tmp22.txt", "høøø")
    extcmd("hg add tmp22.txt")
    extcmd("hg commit -m tmp22")
    thirdhex = extcmd("hg id -i").strip()
    assert push_review_hook_base(root, rbrepo, thirdhex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(thirdhex))
    root = get_root()
    extcmd("hg rebase -d {0}".format(secondhex))
    thirdhex = extcmd("hg id -i").strip()
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(secondhex))
    root = get_root()
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_SUCCESS
    revreq1 = root.get_review_requests(commit_id=rbh.date_author_hash(secondhex),
                                       repository=rbrepo,
                                       status='all')[0]
    assert "tmp21-1" in revreq1.description
    assert "tmp21-2" in revreq1.description
    revreq2 = root.get_review_requests(commit_id=rbh.date_author_hash(thirdhex),
                                       repository=rbrepo,
                                       status='all')[0]
    assert "tmp22" in revreq2.description


def test_hook_base_nopublish():
    root = get_root()
    from mercurial import hg, ui
    os.chdir(TEST_REPO_PATH)
    # Turn off publishing
    write_to_file(".hg/hgrc", "[reviewboardhook]\n"
                  + "ticket_url = http://none/\n"
                  + "publish = False\n")
    write_to_file("tmp30.txt", "høæå")
    extcmd("hg add tmp30.txt")
    extcmd("hg commit -m tmp30øæ")
    firsthex = extcmd("hg id -i").strip()
    rbrepo = get_repo_id(root)
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    firsthash = rbh.date_author_hash(firsthex)
    revreq = root.get_review_requests(commit_id=firsthash,
                                      repository=rbrepo)[0]
    draft = revreq.get_draft()
    assert draft.summary == u"tmp30øæ"
    assert u"tmp30øæ" in draft.description
    write_to_file("tmp30.txt", "høæåææ")
    extcmd("hg commit -m tmp30-2øæ")
    secondhex = extcmd("hg id -i").strip()
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    secondhash = rbh.date_author_hash(secondhex)
    revreq2 = root.get_review_requests(commit_id=firsthash,
                                       repository=rbrepo)[0]
    assert revreq.id == revreq2.id
    draft = revreq2.get_draft()
    assert draft.summary == u"tmp30øæ"
    assert u"tmp30øæ" in draft.description
    assert u"tmp30-2øæ" in draft.description
    draft.update(public=True)
    revreq = root.get_review_requests(commit_id=secondhash,
                                      repository=rbrepo)[0]
    root = get_admin_root()
    approve_revreq(root, rbrepo, secondhash)
    root = get_root()
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_SUCCESS
    write_to_file(".hg/hgrc", "[reviewboardhook]\n"
                  + "ticket_url = http://none/\n"
                  + "publish = True\n")
