"""The start-up check for a newer version on GitHub.

No network: the GitHub calls are replaced by a stand-in, and the .git
folders are built in a temporary directory, so these cover reading the
local commit, naming the remote and deciding what the banner says.
"""

from __future__ import annotations

import io
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests.support import Value, import_gui

import update_check
from update_check import UPSTREAM_REPO, UpdateStatus, check, local_head, upstream_remote

G = import_gui()

SHA_A = "a" * 40
SHA_B = "b" * 40


class Repo:
    """A throwaway directory with just enough of a .git folder."""

    def __init__(self, test: unittest.TestCase):
        handle = tempfile.TemporaryDirectory()
        test.addCleanup(handle.cleanup)
        self.root = Path(handle.name)
        self.git = self.root / ".git"
        self.git.mkdir()

    def write(self, relative: str, text: str) -> None:
        path = self.git / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def http_404(*_args):
    raise urllib.error.HTTPError("url", 404, "Not Found", {}, io.BytesIO(b""))


class LocalHead(unittest.TestCase):
    def test_loose_ref(self):
        repo = Repo(self)
        repo.write("HEAD", "ref: refs/heads/main\n")
        repo.write("refs/heads/main", SHA_A + "\n")
        self.assertEqual(local_head(repo.root), SHA_A)

    def test_packed_ref(self):
        # After a gc the branch only exists in packed-refs.
        repo = Repo(self)
        repo.write("HEAD", "ref: refs/heads/main\n")
        repo.write("packed-refs", f"# pack-refs with: peeled\n{SHA_B} refs/heads/main\n")
        self.assertEqual(local_head(repo.root), SHA_B)

    def test_detached_head(self):
        repo = Repo(self)
        repo.write("HEAD", SHA_A + "\n")
        self.assertEqual(local_head(repo.root), SHA_A)

    def test_not_a_checkout(self):
        # A zip download or a packaged exe: no banner, no error.
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(local_head(Path(folder)))
            self.assertIsNone(check(Path(folder), get=self.fail))

    def test_unborn_branch(self):
        repo = Repo(self)
        repo.write("HEAD", "ref: refs/heads/main\n")
        self.assertIsNone(local_head(repo.root))


class UpstreamRemote(unittest.TestCase):
    def config(self, url: str) -> Path:
        repo = Repo(self)
        repo.write(
            "config",
            '[core]\n\tbare = false\n[remote "fork"]\n\turl = https://github.com/x/'
            'MHWilds-build-optimiser.git\n[remote "upstream"]\n\turl = ' + url + "\n",
        )
        return repo.root

    def test_https_url(self):
        root = self.config(f"https://github.com/{UPSTREAM_REPO}.git")
        self.assertEqual(upstream_remote(root), "upstream")

    def test_ssh_url_any_case(self):
        root = self.config(f"git@github.com:{UPSTREAM_REPO.lower()}")
        self.assertEqual(upstream_remote(root), "upstream")

    def test_no_matching_remote(self):
        root = self.config("https://github.com/someone/else.git")
        self.assertEqual(upstream_remote(root), "")


class Check(unittest.TestCase):
    def repo(self) -> Path:
        repo = Repo(self)
        repo.write("HEAD", "ref: refs/heads/main\n")
        repo.write("refs/heads/main", SHA_A + "\n")
        repo.write("config", f'[remote "origin"]\n\turl = https://github.com/{UPSTREAM_REPO}.git\n')
        return repo.root

    def test_behind(self):
        asked = []

        def get(path):
            asked.append(path)
            return {"ahead_by": 3, "behind_by": 0, "commits": [{"sha": SHA_B}]}

        status = check(self.repo(), get=get)
        # Upstream is the head of the comparison, so its ahead_by is how far
        # the local copy is behind.
        self.assertEqual(asked, [f"/repos/{UPSTREAM_REPO}/compare/{SHA_A}...main"])
        self.assertTrue(status.out_of_date)
        self.assertEqual(status.behind, 3)
        self.assertIn("3 commits behind", status.message())
        self.assertIn("git pull origin main", status.message())

    def test_up_to_date_or_ahead(self):
        status = check(self.repo(), get=lambda _p: {"ahead_by": 0, "behind_by": 2, "commits": []})
        self.assertFalse(status.out_of_date)

    def test_offline_is_no_answer(self):
        def get(_path):
            raise urllib.error.URLError("offline")

        self.assertIsNone(check(self.repo(), get=get))

    def test_rate_limited_is_no_answer(self):
        def get(_path):
            raise urllib.error.HTTPError("url", 403, "rate limited", {}, io.BytesIO(b""))

        self.assertIsNone(check(self.repo(), get=get))

    def test_unpushed_commit_falls_back_to_git(self):
        # GitHub has never seen the local commit: ask git whether upstream's
        # latest is already in the local history.
        calls = iter([http_404, lambda _p: {"sha": SHA_B}])

        def get(path):
            return next(calls)(path)

        with mock.patch.object(update_check, "is_ancestor", return_value=False) as asked:
            status = check(self.repo(), get=get)
        asked.assert_called_once()
        self.assertEqual(asked.call_args.args[0], SHA_B)
        self.assertTrue(status.out_of_date)
        self.assertIn("some commits behind", status.message())

        calls = iter([http_404, lambda _p: {"sha": SHA_B}])
        with mock.patch.object(update_check, "is_ancestor", return_value=True):
            self.assertFalse(check(self.repo(), get=get).out_of_date)

        calls = iter([http_404, lambda _p: {"sha": SHA_B}])
        with mock.patch.object(update_check, "is_ancestor", return_value=None):
            self.assertIsNone(check(self.repo(), get=get))

    def test_message_without_a_matching_remote_names_the_url(self):
        status = UpdateStatus(local_sha=SHA_A, behind=1)
        self.assertIn("1 commit behind", status.message())
        self.assertIn(f"https://github.com/{UPSTREAM_REPO}.git main", status.message())


class Banner(unittest.TestCase):
    def stub(self):
        banner = mock.MagicMock()
        return types.SimpleNamespace(
            update_status=None,
            update_text_var=Value(),
            update_banner=banner,
            notebook=object(),
            _apply_window_size=mock.MagicMock(),
        )

    def test_out_of_date_shows_the_banner(self):
        stub = self.stub()
        status = UpdateStatus(local_sha=SHA_A, behind=2, remote="origin")
        G.SkillsGui._show_update_status(stub, status)
        stub.update_banner.pack.assert_called_once()
        # Sized again, or the banner's height comes off the bottom of the tab.
        stub._apply_window_size.assert_called_once()
        self.assertEqual(stub.update_text_var.get(), status.message())

    def test_current_or_unknown_shows_nothing(self):
        for status in (None, UpdateStatus(local_sha=SHA_A, behind=0)):
            stub = self.stub()
            G.SkillsGui._show_update_status(stub, status)
            stub.update_banner.pack.assert_not_called()
            self.assertEqual(stub.update_text_var.get(), "")


if __name__ == "__main__":
    unittest.main()
