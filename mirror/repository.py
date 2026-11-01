"""Git operations for the generated mirror repository."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import os
from dataclasses import dataclass
from pathlib import Path


class RepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContentRecord:
    commit: str
    community_id: str
    version: str
    skill_dir: str


TRAILERS = {
    "community_id": re.compile(r"^Learnova-Community-Id:\s*(.+?)\s*$", re.MULTILINE),
    "version": re.compile(r"^Learnova-Version:\s*(.+?)\s*$", re.MULTILINE),
    "skill_dir": re.compile(r"^Learnova-Skill-Dir:\s*(.+?)\s*$", re.MULTILINE),
}


class MirrorRepository:
    def __init__(self, path: Path, *, remote: str = "origin", branch: str = "main"):
        self.path = path.resolve()
        self.remote = remote
        self.branch = branch

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise RepositoryError(f"git {' '.join(args)} failed: {detail}")
        return proc

    def verify(self, *, recover_dirty: bool = False, require_identity: bool = True) -> None:
        if not self.path.is_dir():
            raise RepositoryError(f"mirror repo not found: {self.path}")
        self._run("rev-parse", "--git-dir")
        branch = self._run("symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
        if branch != self.branch:
            raise RepositoryError(f"mirror repo branch is {branch!r}, expected {self.branch!r}")
        if self.is_dirty():
            if not recover_dirty:
                raise RepositoryError("mirror repo has uncommitted changes")
            if self.has_head():
                self._run("reset", "--hard", "HEAD")
            self._run("clean", "-fd")
        if require_identity and self._run("var", "GIT_AUTHOR_IDENT", check=False).returncode != 0:
            raise RepositoryError("git commit identity is not configured")

    def restore_worktree(self) -> None:
        if self.has_head():
            self._run("reset", "--hard", "HEAD")
        else:
            self._run("read-tree", "--empty")
        self._run("clean", "-fd")

    def has_head(self) -> bool:
        return self._run("rev-parse", "--verify", "HEAD", check=False).returncode == 0

    def is_dirty(self) -> bool:
        return bool(self._run("status", "--porcelain", "--untracked-files=all").stdout.strip())

    def remote_exists(self) -> bool:
        return self._run("remote", "get-url", self.remote, check=False).returncode == 0

    def prepare_remote(self, *, no_push: bool) -> None:
        if not self.remote_exists():
            if no_push:
                return
            raise RepositoryError(f"git remote does not exist: {self.remote}")

        exists = self._run("ls-remote", "--exit-code", "--heads", self.remote, self.branch, check=False)
        if exists.returncode == 2:
            if self.has_head():
                return
            return
        if exists.returncode != 0:
            detail = (exists.stderr or exists.stdout).strip()
            raise RepositoryError(f"cannot inspect remote branch: {detail}")

        self._run("fetch", self.remote, self.branch)
        remote_ref = "FETCH_HEAD"
        if not self.has_head():
            self._run("reset", "--hard", remote_ref)
            return
        if self._run("merge-base", "--is-ancestor", remote_ref, "HEAD", check=False).returncode == 0:
            return
        if self._run("merge-base", "--is-ancestor", "HEAD", remote_ref, check=False).returncode == 0:
            self._run("merge", "--ff-only", remote_ref)
            return
        raise RepositoryError("local mirror history diverged from remote branch")

    def history_records(self) -> list[ContentRecord]:
        if not self.has_head():
            return []
        raw = self._run("log", "--format=%H%x00%B%x00").stdout
        parts = raw.split("\x00")
        records: list[ContentRecord] = []
        for i in range(0, len(parts) - 1, 2):
            commit, message = parts[i].strip(), parts[i + 1]
            if not commit:
                continue
            values = {}
            for key, pattern in TRAILERS.items():
                match = pattern.search(message)
                if match:
                    values[key] = match.group(1)
            if not values:
                continue
            if set(values) != set(TRAILERS):
                raise RepositoryError(f"incomplete Learnova trailers in commit {commit}")
            records.append(ContentRecord(commit=commit, **values))
        return records

    def commit_exists(self, commit: str) -> bool:
        return self._run("cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode == 0

    def replace_skill(self, skill_dir: str, source: Path) -> None:
        target = self.path / skill_dir
        with tempfile.TemporaryDirectory(prefix="learnova-mirror-stage-", dir=self.path.parent) as temp:
            staged = Path(temp) / "skill"
            shutil.copytree(source, staged)
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(staged, target)

    def remove_skill(self, skill_dir: str) -> None:
        target = self.path / skill_dir
        if target.exists():
            shutil.rmtree(target)

    def commit_publish(self, *, skill_dir: str, community_id: str, version: str) -> str:
        # Each publish commit must store this version's real contents, including
        # when consecutive versions have equal-size files and racy timestamps.
        self._run("rm", "-r", "--cached", "--ignore-unmatch", "--", skill_dir)
        self._run("add", "-A", "--", skill_dir)
        message = (
            f"Learnova-Community-Id: {community_id}\n"
            f"Learnova-Version: {version}\n"
            f"Learnova-Skill-Dir: {skill_dir}"
        )
        self._run(
            "commit",
            "--allow-empty",
            "-m",
            f"mirror: publish {skill_dir}@{version}",
            "-m",
            message,
        )
        return self._run("rev-parse", "HEAD").stdout.strip()

    def commit_state(self, paths: list[str]) -> str | None:
        if not paths:
            return None
        self._run("add", "-A", "--", *sorted(set(paths)))
        if self._run("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        self._run("commit", "-m", "mirror: reconcile published state")
        return self._run("rev-parse", "HEAD").stdout.strip()

    def commit_index(self) -> str | None:
        self._run("add", "-A", "--", "index.json", "SPEC.md")
        if self._run("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        self._run("commit", "-m", "mirror: update index")
        return self._run("rev-parse", "HEAD").stdout.strip()

    def push(self) -> None:
        self._run("push", self.remote, f"HEAD:{self.branch}")

    def needs_push(self) -> bool:
        if not self.remote_exists() or not self.has_head():
            return False
        remote = self._run(
            "ls-remote",
            "--exit-code",
            "--heads",
            self.remote,
            self.branch,
            check=False,
        )
        if remote.returncode == 2:
            return True
        if remote.returncode != 0:
            detail = (remote.stderr or remote.stdout).strip()
            raise RepositoryError(f"cannot inspect remote branch: {detail}")
        remote_hash = remote.stdout.split()[0]
        local_hash = self._run("rev-parse", "HEAD").stdout.strip()
        return remote_hash != local_hash
