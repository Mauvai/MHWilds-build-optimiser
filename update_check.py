"""Is this checkout behind the main repository on GitHub?

The GUI asks once at start-up, on a background thread, and shows a banner
when the answer is yes. Everything here fails quietly to "don't know":
offline, rate-limited, a copy with no .git folder (a zip download, or a
packaged exe), or a commit GitHub has never seen all mean no banner rather
than an error dialog before the window is even up.

The local commit is read straight from the .git folder rather than by
running git, so the check works where git is not on PATH; git is only
asked as a fallback, for the one question the files cannot answer cheaply
(see is_ancestor).
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from load_data import DATA_DIR

UPSTREAM_REPO = "Mauvai/MHWilds-build-optimiser"
UPSTREAM_BRANCH = "main"
API = "https://api.github.com"
TIMEOUT = 6  # seconds; a slow answer is no answer, the GUI does not wait on it
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class UpdateStatus:
    """What the check found. behind is None when it could not tell."""

    local_sha: str
    upstream_sha: str = ""
    behind: int | None = None
    # The local remote that points at UPSTREAM_REPO, so the banner can name
    # the exact pull command; empty when no remote does.
    remote: str = ""

    @property
    def out_of_date(self) -> bool:
        return bool(self.behind)

    def message(self) -> str:
        count = (
            f"{self.behind} commit{'s' if self.behind != 1 else ''}"
            if self.behind and self.behind > 0
            else "some commits"
        )
        if self.remote:
            command = f"git pull {self.remote} {UPSTREAM_BRANCH}"
        else:
            command = f"git pull https://github.com/{UPSTREAM_REPO}.git {UPSTREAM_BRANCH}"
        return (
            f"Update available: this copy is {count} behind {UPSTREAM_REPO} "
            f"({UPSTREAM_BRANCH}). Pull the main repo to update: {command}"
        )


# --- the local side -----------------------------------------------------------


def git_dir(repo: Path = DATA_DIR) -> Path | None:
    """The .git folder, following the 'gitdir:' file a worktree leaves instead."""
    dot_git = repo / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            target = Path(text[len("gitdir:"):].strip())
            return target if target.is_absolute() else (repo / target).resolve()
    return None


def _ref_sha(gitdir: Path, ref: str) -> str | None:
    """A ref's commit, loose file first, then packed-refs."""
    # A worktree keeps HEAD to itself but shares refs with the main
    # repository, which its 'commondir' file names.
    common = gitdir
    commondir = gitdir / "commondir"
    if commondir.is_file():
        common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
    for base in (gitdir, common):
        loose = base / ref
        if loose.is_file():
            sha = loose.read_text(encoding="utf-8").strip()
            if _SHA.match(sha):
                return sha
    packed = common / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref and _SHA.match(parts[0]):
                return parts[0]
    return None


def local_head(repo: Path = DATA_DIR) -> str | None:
    """The checked-out commit, or None if this is not a git checkout."""
    gitdir = git_dir(repo)
    if gitdir is None:
        return None
    try:
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            return _ref_sha(gitdir, head[len("ref:"):].strip())
    except OSError:
        return None
    return head if _SHA.match(head) else None  # detached HEAD


def upstream_remote(repo: Path = DATA_DIR) -> str:
    """Name of the remote whose URL is UPSTREAM_REPO, or ''.

    Matched on owner/name alone so https and ssh URLs, with or without
    '.git', all count. Case-insensitive, as GitHub is.
    """
    gitdir = git_dir(repo)
    if gitdir is None:
        return ""
    config = gitdir / "config"
    if not config.is_file():
        commondir = gitdir / "commondir"
        if not commondir.is_file():
            return ""
        config = (gitdir / commondir.read_text(encoding="utf-8").strip() / "config").resolve()
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return ""
    wanted = UPSTREAM_REPO.lower()
    remote = ""
    for line in text.splitlines():
        line = line.strip()
        section = re.match(r'^\[remote "(.+)"\]$', line)
        if section:
            remote = section.group(1)
            continue
        if line.startswith("["):
            remote = ""
            continue
        if remote and line.replace(" ", "").startswith("url="):
            url = line.split("=", 1)[1].strip().lower()
            url = url[:-4] if url.endswith(".git") else url
            if url.endswith("/" + wanted) or url.endswith(":" + wanted):
                return remote
    return ""


def is_ancestor(commit: str, repo: Path = DATA_DIR) -> bool | None:
    """Is commit part of the local history? None if git cannot say.

    Only asked when GitHub does not know the local commit, i.e. there are
    local commits never pushed. Then the question becomes whether upstream's
    latest commit is already in here; git answers that read-only, without
    fetching. Missing git, or a commit the local repo has never seen (which
    is itself the answer: not fetched, so not merged), both land here.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "HEAD"],
            capture_output=True,
            timeout=TIMEOUT,
            # No console window flashing up under pythonw on Windows.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    # An unknown commit was never fetched, so it is certainly not merged.
    # Any other failure (not a repository, say) is no answer at all.
    if b"not a valid commit" in result.stderr.lower():
        return False
    return None


# --- the GitHub side ------------------------------------------------------------


def _get(path: str) -> dict:
    request = urllib.request.Request(
        API + path,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MHWilds-build-optimiser",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def check(repo: Path = DATA_DIR, get=_get) -> UpdateStatus | None:
    """Compare the local commit with upstream. None when it cannot tell.

    GitHub's compare endpoint answers in one request how far the local
    commit is behind the branch, and works for commits pushed to a fork as
    well, since a fork network shares its objects. A 404 means GitHub has
    never seen the local commit; is_ancestor then decides.
    """
    local = local_head(repo)
    if local is None:
        return None
    status = UpdateStatus(local_sha=local, remote=upstream_remote(repo))
    try:
        compare = get(f"/repos/{UPSTREAM_REPO}/compare/{local}...{UPSTREAM_BRANCH}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return None
        compare = None
    except (OSError, ValueError):
        return None

    if compare is not None:
        status.behind = int(compare.get("ahead_by", 0))  # ahead of local = local is behind
        commits = compare.get("commits") or []
        if commits:
            status.upstream_sha = commits[-1].get("sha", "")
        return status

    try:
        branch = get(f"/repos/{UPSTREAM_REPO}/commits/{UPSTREAM_BRANCH}")
    except (OSError, ValueError):
        return None
    status.upstream_sha = str(branch.get("sha", ""))
    if not _SHA.match(status.upstream_sha):
        return None
    merged = is_ancestor(status.upstream_sha, repo)
    if merged is None:
        return None
    # The count is unknown here (GitHub cannot count from a commit it has not
    # seen), so -1 stands for "behind by an unknown number".
    status.behind = 0 if merged else -1
    return status
