#!/usr/bin/env python3
"""One-shot Learnova community -> Git mirror synchronizer."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mirror.index import build_index, load_index, mappings_from_index, write_index
from mirror.repository import ContentRecord, MirrorRepository, RepositoryError
from mirror.source import ApprovedSkill, load_approved, validate_skill_tree


SPEC_SOURCE = Path(__file__).resolve().with_name("SPEC.md")
LOCK_STALE_SECONDS = 6 * 60 * 60


class SyncError(RuntimeError):
    pass


def _valid_skill_dir(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise SyncError(f"unsafe skill dir: {value!r}")
    return value


class MirrorLock:
    def __init__(self, repo: Path):
        self.path = repo.resolve().with_name(repo.resolve().name + ".mirror.lock")
        self.recovered_stale = False

    def _owner(self) -> dict:
        try:
            return json.loads((self.path / "owner.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _is_stale(self) -> bool:
        owner = self._owner()
        started = float(owner.get("started_at", 0))
        if os.name == "posix" and owner.get("host") == socket.gethostname():
            try:
                os.kill(int(owner.get("pid", -1)), 0)
                return False
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            except (OSError, ValueError):
                pass
        if time.time() - started > LOCK_STALE_SECONDS:
            return True
        return False

    def __enter__(self) -> "MirrorLock":
        try:
            self.path.mkdir()
        except FileExistsError:
            if not self._is_stale():
                raise SyncError(f"mirror is already locked: {self.path}")
            shutil.rmtree(self.path)
            self.path.mkdir()
            self.recovered_stale = True
        (self.path / "owner.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "started_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def _merge_history(
    records: list[ContentRecord],
    dirs: dict[str, str],
    commits: dict[tuple[str, str], str],
) -> None:
    for record in records:
        old_dir = dirs.get(record.community_id)
        if old_dir is not None and old_dir != record.skill_dir:
            raise SyncError(f"community_id {record.community_id} changed mirror dir")
        dirs[record.community_id] = record.skill_dir
        key = (record.community_id, record.version)
        old_commit = commits.get(key)
        if old_commit is not None and old_commit != record.commit:
            raise SyncError(f"{record.skill_dir}@{record.version} maps to multiple commits")
        commits[key] = record.commit


def _allocate_dirs(skills: list[ApprovedSkill], existing: dict[str, str]) -> dict[str, str]:
    result = dict(existing)
    used: dict[str, str] = {}
    for cid, skill_dir in result.items():
        skill_dir = _valid_skill_dir(skill_dir)
        if skill_dir in used and used[skill_dir] != cid:
            raise SyncError(f"mirror dir is shared by multiple community ids: {skill_dir}")
        used[skill_dir] = cid

    ordered = sorted(skills, key=lambda item: (item.versions[0].created_at, item.community_id))
    for skill in ordered:
        if skill.community_id in result:
            continue
        base = _valid_skill_dir(skill.name)
        candidate = base
        if candidate in used and used[candidate] != skill.community_id:
            candidate = _valid_skill_dir(f"{base}--{skill.community_id[:4]}")
        if candidate in used and used[candidate] != skill.community_id:
            raise SyncError(f"cannot allocate unique mirror dir for {skill.community_id}")
        result[skill.community_id] = candidate
        used[candidate] = skill.community_id
    return result


def _write_spec(repo: Path) -> None:
    if not SPEC_SOURCE.is_file():
        raise SyncError(f"mirror spec not found: {SPEC_SOURCE}")
    text = SPEC_SOURCE.read_text(encoding="utf-8")
    target = repo / "SPEC.md"
    if not target.exists() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")


def sync_once(
    *,
    database: Path,
    archive_root: Path,
    repo_path: Path,
    remote: str,
    branch: str,
    dry_run: bool,
    no_push: bool,
) -> dict:
    skills = load_approved(database, archive_root)
    repo = MirrorRepository(repo_path, remote=remote, branch=branch)

    with MirrorLock(repo_path) as lock:
        repo.verify(recover_dirty=lock.recovered_stale, require_identity=not dry_run)
        if not dry_run:
            repo.prepare_remote(no_push=no_push)
        previous = load_index(repo.path / "index.json")
        dirs, commits = mappings_from_index(previous)
        _merge_history(repo.history_records(), dirs, commits)
        for (community_id, version), commit in commits.items():
            if not repo.commit_exists(commit):
                raise SyncError(f"{community_id}@{version} points to missing commit {commit}")
        dirs = _allocate_dirs(skills, dirs)

        new_versions = [
            (skill, version)
            for skill in skills
            for version in skill.versions
            if (skill.community_id, version.version) not in commits
        ]
        for skill, version in new_versions:
            validate_skill_tree(version.archive_dir)
        for skill in skills:
            validate_skill_tree(skill.latest.archive_dir)

        if dry_run:
            return {
                "approved_skills": len(skills),
                "new_versions": len(new_versions),
                "dry_run": True,
                "pushed": False,
            }

        try:
            content_commits = 0
            for skill, version in sorted(
                new_versions,
                key=lambda item: (item[1].created_at, item[0].community_id, item[1].version),
            ):
                skill_dir = dirs[skill.community_id]
                repo.replace_skill(skill_dir, version.archive_dir)
                commit = repo.commit_publish(
                    skill_dir=skill_dir,
                    community_id=skill.community_id,
                    version=version.version,
                )
                commits[(skill.community_id, version.version)] = commit
                content_commits += 1

            desired_dirs = {dirs[skill.community_id] for skill in skills}
            state_paths: list[str] = []
            for skill in skills:
                skill_dir = dirs[skill.community_id]
                repo.replace_skill(skill_dir, skill.latest.archive_dir)
                state_paths.append(skill_dir)
            for known_dir in sorted(set(dirs.values()) - desired_dirs):
                if (repo.path / known_dir).exists():
                    repo.remove_skill(known_dir)
                    state_paths.append(known_dir)
            state_commit = repo.commit_state(state_paths)

            value = build_index(skills, dirs, commits, previous)
            write_index(repo.path / "index.json", value)
            _write_spec(repo.path)
            index_commit = repo.commit_index()

            pushed = False
            if not no_push and repo.needs_push():
                repo.push()
                pushed = True
            return {
                "approved_skills": len(skills),
                "new_versions": len(new_versions),
                "content_commits": content_commits,
                "state_commit": state_commit,
                "index_commit": index_commit,
                "dry_run": False,
                "pushed": pushed,
            }
        except Exception:
            repo.restore_worktree()
            raise


def _env_or(value: str | None, name: str, default: str | None = None) -> str | None:
    return value if value is not None else os.environ.get(name, default)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronize APPROVED Learnova skills into a Git mirror")
    parser.add_argument("--database")
    parser.add_argument("--archive-root")
    parser.add_argument("--repo")
    parser.add_argument("--remote")
    parser.add_argument("--branch")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database = _env_or(args.database, "LEARNOVA_MIRROR_DATABASE")
    archive_root = _env_or(args.archive_root, "LEARNOVA_MIRROR_ARCHIVE_ROOT")
    repo = _env_or(args.repo, "LEARNOVA_MIRROR_REPO")
    remote = _env_or(args.remote, "LEARNOVA_MIRROR_REMOTE", "origin")
    branch = _env_or(args.branch, "LEARNOVA_MIRROR_BRANCH", "main")
    missing = [
        name
        for name, value in (("--database", database), ("--archive-root", archive_root), ("--repo", repo))
        if not value
    ]
    if missing:
        _parser().error("missing required configuration: " + ", ".join(missing))
    try:
        result = sync_once(
            database=Path(database),
            archive_root=Path(archive_root),
            repo_path=Path(repo),
            remote=str(remote),
            branch=str(branch),
            dry_run=args.dry_run,
            no_push=args.no_push,
        )
    except Exception as exc:
        print(f"mirror sync failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
