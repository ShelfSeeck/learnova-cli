"""Mirror producer 端到端测试：临时 SQLite + archived_skill + 真实 Git repo。"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC = REPO_ROOT / "mirror" / "sync.py"


class MirrorRuntimeLayoutTest(unittest.TestCase):
    def test_runtime_spec_copy_matches_development_spec(self):
        self.assertEqual(
            (REPO_ROOT / "mirror" / "SPEC.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "mirror-spec.md").read_text(encoding="utf-8"),
        )


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.stdout.strip()


def _init_git(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Mirror Test")
    _git(repo, "config", "user.email", "mirror-test@example.invalid")


def _init_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE community_skills (
            id TEXT PRIMARY KEY,
            owner_uuid TEXT NOT NULL,
            name TEXT NOT NULL,
            display_name TEXT,
            description TEXT NOT NULL,
            admin_uuids TEXT NOT NULL DEFAULT '[]',
            likes INTEGER NOT NULL DEFAULT 0,
            downloads INTEGER NOT NULL DEFAULT 0,
            latest_version TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE community_skill_versions (
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            version TEXT NOT NULL,
            readme_md TEXT DEFAULT '',
            changelog TEXT DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            archive_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            downloads INTEGER NOT NULL DEFAULT 0,
            source TEXT,
            status TEXT NOT NULL,
            submitted_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(skill_id, version)
        );
        """
    )
    return con


def _add_skill(
    con: sqlite3.Connection,
    root: Path,
    *,
    community_id: str,
    name: str,
    versions: list[tuple[str, str, float]],
    display_name: str | None = None,
    description: str = "demo",
    likes: int = 0,
    downloads: int = 0,
) -> None:
    now = min(created_at for _, _, created_at in versions)
    con.execute(
        """INSERT INTO community_skills
           (id, owner_uuid, name, display_name, description, likes, downloads,
            latest_version, created_at, updated_at)
           VALUES (?, 'owner', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            community_id,
            name,
            display_name,
            description,
            likes,
            downloads,
            versions[-1][0],
            now,
            max(created_at for _, _, created_at in versions),
        ),
    )
    for i, (version, status, created_at) in enumerate(versions):
        archive_rel = Path("archived_skill") / community_id / version
        skill_dir = root / archive_rel / "skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\nversion: {version}\ndescription: demo\n---\n\n# {name} {version}\n",
            encoding="utf-8",
        )
        (skill_dir / "payload.txt").write_text(f"{community_id}:{version}\n", encoding="utf-8")
        con.execute(
            """INSERT INTO community_skill_versions
               (id, skill_id, version, readme_md, changelog, tags, archive_path,
                size_bytes, status, submitted_by, created_at)
               VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, 'owner', ?)""",
            (
                f"{community_id}-{i}",
                community_id,
                version,
                f"changes {version}",
                json.dumps(["demo", version]),
                archive_rel.as_posix(),
                100 + i,
                status,
                created_at,
            ),
        )
    con.commit()


class MirrorSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="learnova-mirror-test-")
        self.root = Path(self.tmp.name)
        self.db = self.root / "project.db"
        self.archive = self.root / "archived_skill"
        self.repo = self.root / "mirror"
        _init_git(self.repo)
        self.con = _init_db(self.db)

    def tearDown(self) -> None:
        self.con.close()
        self.tmp.cleanup()

    def run_sync(
        self,
        *,
        expect_exit: int = 0,
        no_push: bool = True,
        dry_run: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        args = [
            sys.executable,
            str(SYNC),
            "--database",
            str(self.db),
            "--archive-root",
            str(self.archive),
            "--repo",
            str(self.repo),
        ]
        if no_push:
            args.append("--no-push")
        if dry_run:
            args.append("--dry-run")
        proc = subprocess.run(
            args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, expect_exit, proc.stdout + proc.stderr)
        return proc

    def test_only_approved_versions_are_mirrored(self) -> None:
        _add_skill(
            self.con,
            self.root,
            community_id="aaaaaaaa-0000-0000-0000-000000000000",
            name="alpha",
            versions=[
                ("1.0.0", "APPROVED", 100.0),
                ("2.0.0", "PENDING_OWNER", 200.0),
            ],
        )

        self.run_sync()

        index = json.loads((self.repo / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["skills"][0]["latest_version"], "1.0.0")
        self.assertEqual([v["version"] for v in index["skills"][0]["versions"]], ["1.0.0"])
        self.assertIn("1.0.0", (self.repo / "alpha" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertNotIn("2.0.0", (self.repo / "alpha" / "SKILL.md").read_text(encoding="utf-8"))

    def test_each_approved_version_gets_content_commit_and_rerun_is_noop(self) -> None:
        cid = "aaaaaaaa-0000-0000-0000-000000000000"
        _add_skill(
            self.con,
            self.root,
            community_id=cid,
            name="alpha",
            versions=[
                ("1.0.0", "APPROVED", 100.0),
                ("1.1.0", "APPROVED", 200.0),
            ],
        )

        self.run_sync()
        index = json.loads((self.repo / "index.json").read_text(encoding="utf-8"))
        versions = index["skills"][0]["versions"]
        self.assertEqual([v["version"] for v in versions], ["1.0.0", "1.1.0"])
        self.assertNotEqual(versions[0]["commit"], versions[1]["commit"])
        for entry in versions:
            body = _git(self.repo, "show", "-s", "--format=%B", entry["commit"])
            self.assertIn(f"Learnova-Community-Id: {cid}", body)
            self.assertIn(f"Learnova-Version: {entry['version']}", body)
            payload = _git(self.repo, "show", f"{entry['commit']}:alpha/payload.txt")
            self.assertEqual(payload, f"{cid}:{entry['version']}")

        before = _git(self.repo, "rev-list", "--count", "HEAD")
        self.run_sync()
        after = _git(self.repo, "rev-list", "--count", "HEAD")
        self.assertEqual(after, before)

    def test_same_name_gets_stable_distinct_dirs_even_after_rename(self) -> None:
        a = "aaaaaaaa-0000-0000-0000-000000000000"
        b = "bbbbbbbb-0000-0000-0000-000000000000"
        _add_skill(self.con, self.root, community_id=a, name="demo", versions=[("1.0.0", "APPROVED", 100.0)])
        _add_skill(self.con, self.root, community_id=b, name="demo", versions=[("1.0.0", "APPROVED", 200.0)])

        self.run_sync()
        index = json.loads((self.repo / "index.json").read_text(encoding="utf-8"))
        dirs = {s["community_id"]: s["dir"] for s in index["skills"]}
        self.assertEqual(dirs[a], "demo")
        self.assertEqual(dirs[b], "demo--bbbb")

        self.con.execute("UPDATE community_skills SET name='renamed-demo' WHERE id=?", (a,))
        self.con.commit()
        self.run_sync()
        index2 = json.loads((self.repo / "index.json").read_text(encoding="utf-8"))
        dirs2 = {s["community_id"]: s["dir"] for s in index2["skills"]}
        names2 = {s["community_id"]: s["name"] for s in index2["skills"]}
        self.assertEqual(dirs2, dirs)
        self.assertEqual(names2[a], "renamed-demo")

    def test_recovers_content_commit_created_before_index(self) -> None:
        cid = "aaaaaaaa-0000-0000-0000-000000000000"
        _add_skill(
            self.con,
            self.root,
            community_id=cid,
            name="alpha",
            versions=[
                ("1.0.0", "APPROVED", 100.0),
                ("1.1.0", "APPROVED", 200.0),
            ],
        )
        source = self.archive / cid / "1.0.0" / "skill"
        (self.repo / "alpha").mkdir()
        for path in source.iterdir():
            (self.repo / "alpha" / path.name).write_bytes(path.read_bytes())
        _git(self.repo, "add", "alpha")
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "commit",
                "-m",
                "mirror: publish alpha@1.0.0",
                "-m",
                f"Learnova-Community-Id: {cid}\nLearnova-Version: 1.0.0\nLearnova-Skill-Dir: alpha",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        existing = _git(self.repo, "rev-parse", "HEAD")

        self.run_sync()

        index = json.loads((self.repo / "index.json").read_text(encoding="utf-8"))
        versions = {v["version"]: v["commit"] for v in index["skills"][0]["versions"]}
        self.assertEqual(versions["1.0.0"], existing)
        self.assertNotEqual(versions["1.1.0"], existing)
        self.assertEqual(
            _git(self.repo, "log", "--all", "--grep=Learnova-Version: 1.0.0", "--format=%H").splitlines(),
            [existing],
        )

    def test_dry_run_does_not_modify_repo(self) -> None:
        _add_skill(
            self.con,
            self.root,
            community_id="aaaaaaaa-0000-0000-0000-000000000000",
            name="alpha",
            versions=[("1.0.0", "APPROVED", 100.0)],
        )

        self.run_sync(dry_run=True)

        self.assertFalse((self.repo / "alpha").exists())
        self.assertFalse((self.repo / "index.json").exists())
        self.assertFalse(_git(self.repo, "status", "--porcelain"))

    def test_existing_local_commits_are_pushed_on_later_run(self) -> None:
        _add_skill(
            self.con,
            self.root,
            community_id="aaaaaaaa-0000-0000-0000-000000000000",
            name="alpha",
            versions=[("1.0.0", "APPROVED", 100.0)],
        )
        bare = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, text=True)
        _git(self.repo, "remote", "add", "origin", str(bare))

        self.run_sync(no_push=True)
        local_head = _git(self.repo, "rev-parse", "HEAD")
        self.assertFalse(_git(self.repo, "ls-remote", "--heads", "origin", "main"))

        self.run_sync(no_push=False)

        remote_head = _git(self.repo, "ls-remote", "--heads", "origin", "main").split()[0]
        self.assertEqual(remote_head, local_head)

    def test_delist_then_relist_reuses_original_dir_and_content_commit(self) -> None:
        cid = "aaaaaaaa-0000-0000-0000-000000000000"
        _add_skill(
            self.con,
            self.root,
            community_id=cid,
            name="alpha",
            versions=[("1.0.0", "APPROVED", 100.0)],
        )
        self.run_sync()
        first_index = json.loads((self.repo / "index.json").read_text(encoding="utf-8"))
        first_version_commit = first_index["skills"][0]["versions"][0]["commit"]
        first_dir = first_index["skills"][0]["dir"]

        self.con.execute("UPDATE community_skill_versions SET status='PENDING_OWNER'")
        self.con.commit()
        self.run_sync()
        empty_index = json.loads((self.repo / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(empty_index["skills"], [])
        self.assertFalse((self.repo / first_dir).exists())

        self.con.execute("UPDATE community_skill_versions SET status='APPROVED'")
        self.con.execute("UPDATE community_skills SET name='renamed-alpha' WHERE id=?", (cid,))
        self.con.commit()
        self.run_sync()

        relisted = json.loads((self.repo / "index.json").read_text(encoding="utf-8"))["skills"][0]
        self.assertEqual(relisted["dir"], first_dir)
        self.assertEqual(relisted["name"], "renamed-alpha")
        self.assertEqual(relisted["versions"][0]["commit"], first_version_commit)
        matching = _git(
            self.repo,
            "log",
            "--all",
            "--grep=Learnova-Version: 1.0.0",
            "--format=%H",
        ).splitlines()
        self.assertEqual(matching, [first_version_commit])

    def test_failed_git_commit_restores_clean_worktree(self) -> None:
        _add_skill(
            self.con,
            self.root,
            community_id="aaaaaaaa-0000-0000-0000-000000000000",
            name="alpha",
            versions=[("1.0.0", "APPROVED", 100.0)],
        )
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        self.run_sync(expect_exit=1)

        self.assertFalse((self.repo / "alpha").exists())
        self.assertFalse((self.repo / "index.json").exists())
        self.assertFalse(_git(self.repo, "status", "--porcelain"))

        hook.unlink()
        self.run_sync()
        self.assertTrue((self.repo / "alpha" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
