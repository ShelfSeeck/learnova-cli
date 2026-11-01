"""Publishing uses synthetic packages and an injected authenticated API."""

import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/learnova-cli/scripts"
sys.path.insert(0, str(SCRIPTS))
import auth
from cli.context import CliContext
from cli.commands import publish, status
from core.errors import AuthError, CliError
import publishing

SKILL = "---\nname: sample\ndescription: A portable sample skill\nmetadata:\n  name: nested-name\n  category: sample\n---\nRead [guide](references/guide.md).\n"


def submission(identifier="version-one", skill_id="skill-one", name="sample", current="submission-one"):
    return {"id": identifier, "skill_id": skill_id, "submitted_by": "author-one", "version": "1.0.0",
            "status": "CHANGES_REQUESTED", "skill_name": name, "metadata": {"name": name},
            "source_library_id": "library-one", "current_submission_id": current,
            "archive_path": "/private/should-not-appear", "unrelated": "opaque"}


class PublishFixture:
    def __init__(self):
        self.calls = []
        self.uploaded = {"id": "library-one", "user_uuid": "author-one", "community_skill_id": None}
        self.form = {"library_skill": copy.deepcopy(self.uploaded), "community_skill": None,
                     "base_submission_id": None, "community_skill_candidates": [{"id": "unrelated-same-name"}]}
        self.published = {**submission(), "status": "PENDING_OWNER", "version": "2.0.0"}
        self.rows = [submission()]
        self.page_size = 1
        self.details = {"submission-one": {"submission": {"id": "submission-one", "version_id": "version-one", "submitted_by": "author-one"},
                        "logs": [{"version_id": "version-one", "submission_id": "submission-one", "action": "RETURN", "stage": "OWNER",
                                  "to_status": "CHANGES_REQUESTED", "note": "Please add examples", "created_at": 1.0,
                                  "reviewer_uuid": "private-reviewer", "request_hash": "private-hash"}],
                        "legacy_logs": [{"note": "unrelated historical note"}], "manifest": {"private": True}}}

    def request(self, ctx, method, path, body=None, **kwargs):
        self.calls.append((method, path, body, kwargs))
        parsed = urlsplit(path)
        if path == "/library/skills/upload":
            return copy.deepcopy(self.uploaded)
        if path.endswith("/publish-form"):
            return copy.deepcopy(self.form)
        if path.endswith("/publish"):
            return copy.deepcopy(self.published)
        if parsed.path == "/library/submissions":
            offset = int(parse_qs(parsed.query)["offset"][0])
            return {"items": copy.deepcopy(self.rows[offset:offset + self.page_size]), "total": len(self.rows), "limit": self.page_size, "offset": offset}
        return copy.deepcopy(self.details[path.rsplit("/", 1)[-1]])


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.skill = self.root / "sample"
        (self.skill / "references").mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(SKILL, newline="\n")
        (self.skill / "references/guide.md").write_text("# Guide\nPortable reference.\n", newline="\n")
        self.ctx = CliContext(cwd=self.root, auth={"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"})
        self.fixture = PublishFixture()
        self.me = patch.object(auth, "current_user", return_value={"uuid": "author-one", "username": "sample-user"})
        self.me.start()
        self.addCleanup(self.me.stop)
        self.requests = patch.object(auth, "request", side_effect=self.fixture.request)
        self.requests.start()
        self.addCleanup(self.requests.stop)

    def assert_code(self, code, callback):
        with self.assertRaises(CliError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_publish_plain_agent_skill_preserves_current_files_and_submits(self):
        (self.skill / "scripts").mkdir()
        (self.skill / "scripts/example.py").write_text("print('sample')\n")
        before = {p.relative_to(self.skill).as_posix(): p.read_bytes() for p in self.skill.rglob("*") if p.is_file()}
        result = publishing.publish(self.ctx, "sample", "2.0.0", "Explicit changes")
        self.assertEqual(result["status"], "PENDING_OWNER")
        self.assertTrue(result["submitted"])
        self.assertEqual(result["name"], "sample")
        self.assertNotIn("published", result)
        upload = self.fixture.calls[0]
        self.assertEqual(upload[:3], ("POST", "/library/skills/upload", None))
        self.assertEqual(upload[3]["content_type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(upload[3]["data"])) as archive:
            self.assertEqual({name: archive.read(name) for name in archive.namelist()}, before)
        self.assertEqual(self.fixture.calls[2][2], {"version": "2.0.0", "changelog": "Explicit changes", "base_submission_id": None, "community_skill_id": None})
        self.assertEqual(before, {p.relative_to(self.skill).as_posix(): p.read_bytes() for p in self.skill.rglob("*") if p.is_file()})

    def test_package_excludes_private_credentials_vcs_and_management_state(self):
        for directory in (".git", ".learnova", ".codex", ".superpowers"):
            (self.skill / directory).mkdir()
            (self.skill / directory / "private").write_text("synthetic-private")
        for name in ("auth.json", ".env", ".env.demo", ".envrc", "credentials.json", "secrets.yaml", "private.pem", "private.key", ".learnova-build.json", ".learnova-install.json"):
            (self.skill / name).write_text("synthetic-private")
        data, _ = publishing.package(self.skill)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {"SKILL.md", "references/guide.md"})
        self.assertNotIn(b"synthetic-private", data)

    def test_yaml_folded_description_and_nested_metadata_are_accepted(self):
        text = SKILL.replace("description: A portable sample skill", "description: >-\n  A portable\n  sample skill")
        (self.skill / "SKILL.md").write_text(text, newline="\n")
        data, metadata = publishing.package(self.skill)
        self.assertEqual(metadata["description"], "A portable sample skill")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read("SKILL.md"), text.encode())

    def test_invalid_metadata_missing_references_and_missing_root_rejected_before_api(self):
        for text in ("not frontmatter", SKILL.replace("name: sample", "name: ../outside"),
                     SKILL.replace("description: A portable sample skill\n", ""), SKILL.replace("references/guide.md", "../private.md"),
                     SKILL.replace("references/guide.md", "references/missing.md"), SKILL.replace("name: sample", "name: sample\nname: another")):
            (self.skill / "SKILL.md").write_text(text, newline="\n")
            self.assert_code("PUBLISH_PACKAGE_INVALID", lambda: publishing.publish(self.ctx, "sample", "2.0.0", "Changes"))
        self.assert_code("PUBLISH_PACKAGE_INVALID", lambda: publishing.package(self.root / "absent"))
        self.assertEqual(self.fixture.calls, [])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symbolic_links_and_symlink_ancestors_are_rejected(self):
        link = self.skill / ".env"
        link.symlink_to(self.skill / "SKILL.md")
        self.assert_code("PUBLISH_PACKAGE_INVALID", lambda: publishing.package(self.skill))
        link.unlink()
        link = self.root / "alias"
        link.symlink_to(self.skill, target_is_directory=True)
        self.assert_code("PUBLISH_PACKAGE_INVALID", lambda: publishing.package(link))

    @unittest.skipUnless(hasattr(os, "link"), "hardlinks unavailable")
    def test_hard_links_and_special_files_are_rejected(self):
        path = self.skill / "references/linked.md"
        os.link(self.skill / "references/guide.md", path)
        self.assert_code("PUBLISH_PACKAGE_INVALID", lambda: publishing.package(self.skill))
        path.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(path)
            self.assert_code("PUBLISH_PACKAGE_INVALID", lambda: publishing.package(self.skill))

    def test_package_limits_and_unsafe_portable_names(self):
        for key, value in (("MAX_PACKAGE_BYTES", 1), ("MAX_PACKAGE_FILES", 1), ("MAX_PACKAGE_DEPTH", 1)):
            with self.subTest(key=key), patch.object(publishing, key, value):
                self.assert_code("PUBLISH_PACKAGE_INVALID", lambda: publishing.package(self.skill))
        path = self.skill / "references/NUL.md"
        path.write_text("unsafe")
        self.assert_code("PUBLISH_PACKAGE_INVALID", lambda: publishing.package(self.skill))

    def test_explicit_version_and_changelog_validation_no_extra_prompt(self):
        for version, changelog in (("v1", "changes"), ("1.0.0", ""), ("1.0.0", "  "), ("1.0.0", "x\x00y")):
            self.assert_code("BAD_USAGE", lambda: publishing.publish(self.ctx, "sample", version, changelog))
        self.assertEqual(self.fixture.calls, [])

    def test_authentication_failure_prevents_upload(self):
        with patch.object(auth, "current_user", side_effect=AuthError("UNAUTHORIZED", "Please log in")):
            self.assert_code("UNAUTHORIZED", lambda: publishing.publish(self.ctx, "sample", "2.0.0", "Changes"))
        self.assertEqual(self.fixture.calls, [])

    def test_foreign_upload_linked_upload_and_invalid_form_prevent_publish(self):
        original = copy.deepcopy(self.fixture.uploaded)
        for updates in ({"user_uuid": "another-author"}, {"community_skill_id": "existing-skill"}, {"id": "../unsafe"}):
            self.fixture.uploaded = {**original, **updates}
            self.assert_code("BACKEND_RESPONSE_INVALID", lambda: publishing.publish(self.ctx, "sample", "2.0.0", "Changes"))
        self.fixture.uploaded = original
        self.fixture.form["base_submission_id"] = "unexpected-base"
        self.assert_code("BACKEND_RESPONSE_INVALID", lambda: publishing.publish(self.ctx, "sample", "2.0.0", "Changes"))
        self.assertFalse(any(path.endswith("/publish") for _, path, _, _ in self.fixture.calls))

    def test_invalid_submission_response_never_claims_success(self):
        for updates in ({"status": "APPROVED"}, {"version": "3.0.0"}, {"submitted_by": "other"}, {"current_submission_id": "bad/path"}):
            with self.subTest(updates=updates):
                self.fixture.published = {**PublishFixture().published, **updates}
                self.assert_code("BACKEND_RESPONSE_INVALID", lambda: publishing.publish(self.ctx, "sample", "2.0.0", "Changes"))

    def test_status_paginates_filters_and_reads_only_matching_details(self):
        self.fixture.rows.append(submission("version-two", "skill-two", "another", None))
        result = publishing.status(self.ctx, "sample")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["submissions"][0]["notes"], ["Please add examples"])
        self.assertEqual(result["submissions"][0]["status"], "CHANGES_REQUESTED")
        queries = [parse_qs(urlsplit(path).query) for _, path, _, _ in self.fixture.calls if path.startswith("/library/submissions")]
        self.assertEqual([q["offset"] for q in queries], [["0"], ["1"]])
        serialized = json.dumps(result)
        for private in ("archive_path", "private-reviewer", "request_hash", "manifest", "unrelated historical note", "submitted_by"):
            self.assertNotIn(private, serialized)

    def test_status_notes_redact_tokens_headers_email_paths_and_urls(self):
        self.fixture.details["submission-one"]["logs"][0]["note"] = ('Fix examples; synthetic-access synthetic-refresh Bearer unknown-token '
            '"refresh_token": "unlisted-secret" password=another-secret https://private.invalid/path?key=value '
            'demo@example.invalid /private/config C:\\private\\config sk-syntheticprivatekey eyJhbGciOiJub25lIn0.c3ludGhldGlj.c2lnbmF0dXJl\x1b')
        result = publishing.status(self.ctx)
        text = json.dumps(result)
        self.assertIn("Fix examples", text)
        for secret in ("synthetic-access", "synthetic-refresh", "unknown-token", "unlisted-secret", "another-secret", "private.invalid", "demo@example.invalid", "/private/config", "C:\\private", "sk-syntheticprivatekey", "eyJhbGci"):
            self.assertNotIn(secret, text)

    def test_empty_status_legacy_status_id_and_library_filters(self):
        self.fixture.rows[0]["current_submission_id"] = None
        for selector in ("skill-one", "library-one", None):
            result = publishing.status(self.ctx, selector)
            self.assertEqual(result["submissions"][0]["notes"], [])
        self.assertEqual(publishing.status(self.ctx, "missing")["total"], 0)
        self.fixture.rows = []
        self.assertEqual(publishing.status(self.ctx), {"submissions": [], "total": 0, "source": "backend"})

    def test_ambiguous_name_requires_id(self):
        self.fixture.rows.append(submission("version-two", "skill-two", "sample", None))
        self.assert_code("SKILL_AMBIGUOUS", lambda: publishing.status(self.ctx, "sample"))
        self.assertEqual(publishing.status(self.ctx, "skill-one")["total"], 1)

    def test_foreign_owner_or_detail_or_logs_are_rejected(self):
        self.fixture.rows[0]["submitted_by"] = "other"
        self.assert_code("BACKEND_RESPONSE_INVALID", lambda: publishing.status(self.ctx))
        self.fixture.rows[0]["submitted_by"] = "author-one"
        detail = self.fixture.details["submission-one"]
        detail["submission"]["version_id"] = "other"
        self.assert_code("BACKEND_RESPONSE_INVALID", lambda: publishing.status(self.ctx))
        detail["submission"]["version_id"] = "version-one"
        detail["logs"][0]["submission_id"] = "other"
        self.assert_code("BACKEND_RESPONSE_INVALID", lambda: publishing.status(self.ctx))

    def test_pagination_invalid_totals_offsets_repeats_empty_pages_and_page_bound(self):
        for response in ({"items": [], "total": True, "limit": 1, "offset": 0},
                         {"items": [], "total": 1, "limit": 1, "offset": 0},
                         {"items": [], "total": 0, "limit": 1, "offset": 1},
                         {"items": [], "total": publishing.MAX_SUBMISSIONS + 1, "limit": 1, "offset": 0}):
            with patch.object(auth, "request", return_value=response):
                self.assert_code("BACKEND_RESPONSE_INVALID", lambda: publishing.status(self.ctx))
        self.fixture.rows.append(copy.deepcopy(self.fixture.rows[0]))
        self.assert_code("BACKEND_RESPONSE_INVALID", lambda: publishing.status(self.ctx))
        self.fixture.rows[1]["id"] = "another-version"
        with patch.object(publishing, "MAX_PAGES", 1):
            self.assert_code("BACKEND_RESPONSE_INVALID", lambda: publishing.status(self.ctx))

    def test_handlers_delegate_without_io(self):
        args = type("Args", (), {"directory": "sample", "version": "2.0.0", "changelog": "Changes", "skill": "sample"})()
        with patch.object(publishing, "publish", return_value={"ok": True}) as call:
            self.assertEqual(publish.handler(args, self.ctx), {"ok": True})
            call.assert_called_once_with(self.ctx, "sample", "2.0.0", "Changes")
        with patch.object(publishing, "status", return_value={"total": 0}) as call:
            self.assertEqual(status.handler(args, self.ctx), {"total": 0})
            call.assert_called_once_with(self.ctx, "sample")


if __name__ == "__main__":
    unittest.main()
