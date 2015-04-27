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
TEST_REPO_PATH = HOOK_PATH + "/ramdisk/" + TEST_REPO_NAME
CLIENT_REPO_NAME = "clientrepo"
CLIENT_REPO_PATH = HOOK_PATH + "/ramdisk/" + CLIENT_REPO_NAME


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


class Resource(object):
    def __contains__(self, key):
        return key in self._fields

    def __getattr__(self, name):
        if name in self._fields:
            return self._fields[name]
        else:
            raise AttributeError

    def get_self(self):
        return self

class ListResource(Resource):
    def num_items(self):
        return len(self._list)

    def __getitem__(self, key):
        return self._list[key]

    def __len__(self):
        return len(self._list)


class FakeRBClient(Resource):
    _root = None
    def __init__(self, url, username=None, password=None, api_token=None):
        if FakeRBClient._root is None:
            FakeRBClient._root = FakeRoot(capabilities=None, username='testuser')
        FakeRBClient._root.update_user(username)

    def get_root(self):
        return FakeRBClient._root


class FakeRoot(Resource):
    def __init__(self, capabilities, username):
        self._fields = {'capabilities': capabilities}
        self._repo_list = FakeRepoList(self)
        self._revreq_list = FakeRevReqList(self)
        self.users = {'testuser': FakeUser('testuser'),
                      'admin': FakeUser('admin')}
        self.update_user(username)

    def update_user(self, username):
        self.user = self.users[username]

    def get_repositories(self, **kwargs):
        return self._repo_list

    def get_review_requests(self, **kwargs):
        for revreq in self._revreq_list:
            if 'id' in kwargs and revreq.id == kwargs['id']:
                return [revreq]
            elif 'commit_id' in kwargs and\
                 revreq.commit_id == kwargs['commit_id']:
                return [revreq]
        return self._revreq_list

    def get_users(self, q, **kwargs):
        return self.users[q]


class FakeUser(Resource):
    next_id = 0
    def __init__(self, username):
        self._fields = {'id': FakeUser.next_id,
                        'username': username}
        FakeUser.next_id += 1


class FakeRepoList(ListResource):
    def __init__(self, root):
        self.root = root
        self._fields = {'num_items': 0}
        self._list = [FakeRepo(root)]

class FakeRepo(Resource):
    def __init__(self, root):
        self.root = root
        self._fields = {'id': 0}

class FakeRevReqList(ListResource):
    def __init__(self, root):
        self.root = root
        self._fields = {}
        self._list = []
        self.next_id = 0

    def create(self, commit_id=None, repository=None, submit_as=None):
        if submit_as is None:
            user = self.root.user
        else:
            user = self.root.get_users(submit_as)
        print "Creating revreq id", self.next_id
        revreq = FakeRevReq(self.next_id, commit_id, user, self)
        self.next_id += 1
        self._list.append(revreq)
        return revreq

    def delete(self, revreq):
        self._list.remove(revreq)

class FakeRevReq(Resource):
    def __init__(self, id, commit_id, user, parent):
        self._fields = {'id': id,
                        'commit_id': commit_id,
                        'user': user,
                        'absolute_url': 'http://%d' % id,
                        'description': '',
                        'summary': '',
                        'extra_data': {},
                        'approved': False,
                        'status': 'pending'}
        self._draft = None
        self._parent = parent
        self.root = parent.root
        self._reviews = FakeReviewList(self.root)

    def get_submitter(self, **kwargs):
        return self.user

    def get_diffs(self, **kwargs):
        return FakeDiffList()

    def update(self, **kwargs):
        for key, value in kwargs.iteritems():
            keyparts = key.split('.')
            if keyparts[0] == 'extra_data':
                self._fields['extra_data'][keyparts[1]] = value

    def get_or_create_draft(self, **kwargs):
        if self._draft is None:
            self._draft = FakeRevReqDraft(self)
        return self._draft

    def get_draft(self, **kwargs):
        return self._draft

    def publish_draft(self):
        for key, value in self._draft._fields.iteritems():
            self._fields[key] = value
        self._draft = None

    def get_reviews(self, **kwargs):
        return self._reviews

    def delete(self):
        self._parent.delete(self)

    def approved(self):
        for review in self._reviews:
            if review.ship_it:
                return True

class FakeRevReqDraft(Resource):
    def __init__(self, revreq):
        self._revreq = revreq
        self._fields = {}

    def update(self, **kwargs):
        for key, value in kwargs.iteritems():
            if key != "publish":
                self._fields[key] = value
        if "public" in kwargs and kwargs['public']:
            self._revreq.publish_draft()

class FakeDiffList(ListResource):
    def __init__(self):
        self._fields = {}
        self._list = [FakeDiff()]

    def upload_diff(self, *args, **kwargs):
        self._list[0].update_timestamp()

from datetime import datetime
class FakeDiff(Resource):
    def __init__(self):
        self._fields = {}
        self.update_timestamp()

    def update_timestamp(self):
        timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
        self._fields = {'timestamp': timestamp}


class FakeReviewList(ListResource):
    def __init__(self, root):
        self.root = root
        self._fields = {}
        self._list = []
        self.next_id = 0

    def create(self):
        self._list.append(FakeReview(self.root.user))
        return self._list[-1]

class FakeReview(Resource):
    def __init__(self, user):
        self._fields = {'ship_it': False, 'public': False,
                        'body_top': "",
                        'user': user}

    def update(self, ship_it=False, public=False, body_top=""):
        self._fields['ship_it'] = ship_it
        self._fields['public'] = public
        self._fields['body_top'] = body_top
        timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
        self._fields['timestamp'] = timestamp

    def get_user(self, only_fields=None, only_links=None):
        return self._fields['user']

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
    write_to_file("diff", diff['diff'])
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
    repo_id = rbh.get_repo_id(root, TEST_REPO_NAME)
    assert repo_id >= 0
    try:
        repo_id2 = rbh.get_repo_id(root, "nonexistingrepo")
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
    assert revreq.branch == "default"
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
    assert revreq.branch == "default"
    assert u"tmp5hållå" in revreq.description
    assert u"tmp5ædd" in revreq.description


def test_hook_base():
    root = get_root()
    from mercurial import hg, ui
    os.chdir(TEST_REPO_PATH)
    write_to_file("tmp6.txt", "høæå")
    extcmd("hg add tmp6.txt")
    extcmd("hg commit -m tmp6 -u user1")
    write_to_file("tmp7.txt", "høæå")
    extcmd("hg add tmp7.txt")
    extcmd("hg commit -m tmp7 -u user2")
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
    """Test that hook allows merges when config says so."""
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
    thirdhex = extcmd("hg id -i").strip()
    assert push_review_hook_base(root, rbrepo, secondhex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_SUCCESS
    rbh.configbool = oldconfigbool
    commit_id = rbh.date_author_hash(thirdhex)
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
    # Create new commit one step back (new branch)
    extcmd("hg up -r -2")
    write_to_file("tmp22.txt", "blæææ")
    extcmd("hg add tmp22.txt")
    extcmd("hg commit -m tmp22-1")
    secondhex = extcmd("hg id -i").strip()
    assert push_review_hook_base(root, rbrepo, secondhex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(firsthex))
    approve_revreq(root, rbrepo, rbh.date_author_hash(secondhex))
    root = get_root()
    # Rebase the new branch on top of the original
    extcmd("hg rebase -d {0}".format(firsthex))
    secondhex = extcmd("hg id -i").strip()
    # First push fails since second changeset ID is changed
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_SUCCESS
    revreq1 = root.get_review_requests(commit_id=rbh.date_author_hash(firsthex),
                                       repository=rbrepo,
                                       status='all')[0]
    assert "tmp21-1" in revreq1.description
    assert revreq1.status == 'submitted'
    revreq2 = root.get_review_requests(commit_id=rbh.date_author_hash(secondhex),
                                       repository=rbrepo,
                                       status='all')[0]
    assert "tmp22-1" in revreq2.description
    assert revreq2.status == 'submitted'


def test_rebase_accepted():
    """Test that hook stops accepted changesets rebased onto others."""
    os.chdir(TEST_REPO_PATH)
    write_to_file("tmp40.txt", "hæææ")
    extcmd("hg add tmp40.txt")
    extcmd("hg commit -m tmp40")
    firsthex = extcmd("hg id -i").strip()
    root = get_root()
    rbrepo = get_repo_id(root)
    assert push_review_hook_base(root, rbrepo, firsthex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    root = get_admin_root()
    approve_revreq(root, rbrepo, rbh.date_author_hash(firsthex))
    root = get_root()
    # Create new commit one step back (new branch)
    extcmd("hg up -r -2")
    write_to_file("tmp41.txt", "blæææ")
    extcmd("hg add tmp41.txt")
    extcmd("hg commit -m tmp41")
    secondhex = extcmd("hg id -i").strip()
    # Rebase first onto the second
    extcmd("hg rebase -s {0} -d {1}".format(firsthex, secondhex))
    assert push_review_hook_base(root, rbrepo, secondhex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED
    assert push_review_hook_base(root, rbrepo, secondhex,
                                 TEST_SERVER, TEST_USER) == rbh.HOOK_FAILED


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
    assert not draft.public
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
    assert not draft.public
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
