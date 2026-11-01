"""Subprocess CLI acceptance against synthetic account/library HTTP services."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import uuid
import zipfile

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills/learnova-cli/scripts"
sys.path.insert(0, str(SCRIPTS))
import auth
from cli.context import CliContext
import store
import telemetry
from fixtures.gitlab_stub import MirrorStub

ACCESS = "synthetic-flow-access"
REFRESH = "synthetic-flow-refresh"
PASSWORD = "synthetic-flow-password"
ROW = {"id": "version-one", "skill_id": "skill-one", "version": "1.0.0", "status": "CHANGES_REQUESTED",
       "skill_name": "sample", "submitted_by": "author-one", "current_submission_id": "submission-one",
       "source_library_id": "library-one", "metadata": {"name": "sample"}, "archive_path": "/private/archive"}


class BackendFixture:
    def __init__(self):
        self.calls = []
        self.refresh_count = 0
        self.reject_refresh = False
        self.upload_status = 201
        self.foreign_status = False
        self.event = threading.Event()
        self.refresh_gate = None
        self.refresh_started = threading.Event()
        self.event_gate = None
        self.event_started = threading.Event()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def record(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                fixture.calls.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": raw})
                return raw

            def send(self, value=None, status=200, cookies=()):
                raw = b"" if value is None else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for cookie in cookies:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                raw = self.record()
                if self.path == "/auth/login":
                    request = json.loads(raw)
                    if request != {"email": "demo@example.invalid", "password": PASSWORD}:
                        self.send({"detail": "synthetic-login-rejected"}, 401)
                    else:
                        self.send({"access_token": ACCESS}, cookies=[f"refresh_token={REFRESH}; HttpOnly; Path=/auth"])
                elif self.path == "/auth/refresh":
                    fixture.refresh_count += 1
                    fixture.refresh_started.set()
                    if fixture.refresh_gate is not None:
                        fixture.refresh_gate.wait(5)
                    if fixture.reject_refresh:
                        self.send({"detail": PASSWORD}, 401)
                    else:
                        self.send({"access_token": ACCESS + str(fixture.refresh_count)},
                                  cookies=[f"refresh_token={REFRESH}{fixture.refresh_count}; HttpOnly; Path=/auth"])
                elif self.path == "/auth/logout":
                    self.send(status=204)
                elif self.path == "/library/skills/upload":
                    if fixture.upload_status != 201:
                        self.send({"detail": {"message": PASSWORD, "access_token": ACCESS}}, fixture.upload_status)
                    else:
                        self.send({"id": "library-one", "user_uuid": "author-one", "community_skill_id": None,
                                   "local_path": "/private/library", "private_token": ACCESS}, 201)
                elif self.path == "/library/skills/library-one/publish":
                    payload = json.loads(raw)
                    self.send({**ROW, "version": payload["version"], "status": "PENDING_OWNER"})
                elif urlsplit(self.path).path == "/events/installations":
                    fixture.event_started.set()
                    if fixture.event_gate is not None:
                        fixture.event_gate.wait(5)
                    fixture.event.set()
                    self.send(status=204)
                else:
                    self.send({"detail": "fixture-route-not-found"}, 404)

            def do_GET(self):
                self.record()
                parsed = urlsplit(self.path)
                if self.path == "/auth/me":
                    self.send({"uuid": "author-one", "username": "sample-user", "email": "demo@example.invalid",
                               "password": PASSWORD, "access_token": ACCESS})
                elif self.path == "/library/skills/library-one/publish-form":
                    self.send({"library_skill": {"id": "library-one", "user_uuid": "author-one", "community_skill_id": None},
                               "base_submission_id": None, "community_skill": None,
                               "community_skill_candidates": [{"id": "existing-same-name", "name": "sample"}]})
                elif parsed.path == "/library/submissions":
                    offset = int(parse_qs(parsed.query)["offset"][0])
                    first = {**ROW, "submitted_by": "another-author" if fixture.foreign_status else "author-one"}
                    rows = [first, {**ROW, "id": "version-two", "skill_id": "skill-two", "skill_name": "other",
                                    "metadata": {"name": "other"}, "current_submission_id": None}]
                    self.send({"items": rows[offset:offset + 1], "total": 2, "limit": 1, "offset": offset})
                elif self.path == "/community/skills/skill-one/versions/version-one/submissions/submission-one":
                    self.send({"submission": {"id": "submission-one", "version_id": "version-one", "submitted_by": "author-one"},
                               "logs": [{"version_id": "version-one", "submission_id": "submission-one", "action": "RETURN", "stage": "OWNER",
                                         "to_status": "CHANGES_REQUESTED", "created_at": 1.0,
                                         "note": 'Add examples; "password": "do-not-disclose" Bearer do-not-disclose-bearer https://private.invalid/key',
                                         "reviewer_uuid": "private-reviewer"}], "archive_path": "/private/archive"})
                elif parsed.path.startswith("/community/"):
                    self.send({"detail": PASSWORD}, 503)
                else:
                    self.send({"detail": "fixture-route-not-found"}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class BackendFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / ".learnova"
        self.state.mkdir()
        (self.root / ".claude").mkdir()
        self.backend = BackendFixture()
        self.addCleanup(self.backend.close)
        self.mirror = MirrorStub(REPO / "tests/fixtures/mirror")
        self.mirror_url = self.mirror.start() + "/sample/skills"
        self.addCleanup(self.mirror.stop)
        self.config = {**store.DEFAULTS, "backend_url": self.backend.url, "mirror_repo_url": self.mirror_url}
        (self.state / "config.json").write_text(json.dumps(self.config))
        index = json.loads((REPO / "tests/fixtures/mirror/index.json").read_text())
        (self.state / "index_cache.json").write_text(json.dumps({"index": index, "mirror_repo_url": self.mirror_url, "fetched_at": time.time()}))
        self.skill = self.root / "sample"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("---\nname: sample\ndescription: A sample skill\n---\nProvide sample advice.\n")
        (self.skill / ".env").write_text("synthetic-local-secret")
        self.ctx = CliContext(config=self.config, cwd=self.root)
        for name, path in (("AUTH_PATH", "auth.json"), ("CONFIG_PATH", "config.json")):
            patcher = patch.object(store, name, self.state / path)
            patcher.start()
            self.addCleanup(patcher.stop)
        auth.login(self.ctx, "demo@example.invalid", PASSWORD)
        self.env = {**os.environ, "HOME": str(self.root), "USERPROFILE": str(self.root), "LEARNOVA_HOME": str(self.state),
                    "LEARNOVA_TELEMETRY": "0", "PYTHONIOENCODING": "utf-8"}

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(SCRIPTS / "learnova.py"), *args, "--json"], cwd=self.root,
                                env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, code, f"Unexpected exit; stdout={result.stdout!r} stderr={result.stderr!r}")
        for private in (PASSWORD, ACCESS, REFRESH, "do-not-disclose", "synthetic-local-secret", "private.invalid", "private-reviewer", "/private/archive"):
            self.assertNotIn(private, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_login_session_publish_status_and_nonce_protocol(self):
        me = self.cli("whoami")
        self.assertTrue(me["logged_in"])
        self.assertEqual(me["username"], "sample-user")
        result = self.cli("publish", "sample", "--version", "2.0.0", "--changelog", "Add examples")
        self.assertEqual((result["status"], result["version"]), ("PENDING_OWNER", "2.0.0"))
        upload = next(c for c in self.backend.calls if c["path"] == "/library/skills/upload")
        self.assertEqual(upload["headers"]["Content-Type"], "application/zip")
        self.assertEqual(int(upload["headers"]["Content-Length"]), len(upload["body"]))
        with zipfile.ZipFile(io.BytesIO(upload["body"])) as archive:
            self.assertEqual(archive.namelist(), ["SKILL.md"])
        publish = next(c for c in self.backend.calls if c["path"].endswith("/publish"))
        self.assertEqual(json.loads(publish["body"]), {"version": "2.0.0", "changelog": "Add examples", "base_submission_id": None, "community_skill_id": None})
        for call in (upload, publish):
            uuid.UUID(call["headers"]["X-Nonce"])
            float(call["headers"]["X-Nonce-Timestamp"])
            self.assertTrue(call["headers"]["Authorization"].startswith("Bearer " + ACCESS))
            self.assertNotIn("Cookie", call["headers"])
        self.assertNotEqual(upload["headers"]["X-Nonce"], publish["headers"]["X-Nonce"])
        review = self.cli("status", "sample")
        self.assertEqual(review["total"], 1)
        self.assertEqual(review["submissions"][0]["status"], "CHANGES_REQUESTED")
        self.assertIn("Add examples", review["submissions"][0]["notes"][0])
        offsets = [parse_qs(urlsplit(c["path"]).query)["offset"] for c in self.backend.calls if c["path"].startswith("/library/submissions")]
        self.assertEqual(offsets, [["0"], ["1"]])
        state = json.loads((self.state / "auth.json").read_text())
        self.assertNotIn("access_token", state)
        self.assertNotIn(PASSWORD, json.dumps(state))

    def test_error_codes_and_redaction_from_http_auth_and_usage(self):
        self.backend.upload_status = 422
        self.assertEqual(self.cli("publish", "sample", "--version", "2.0.0", "--changelog", "Changes", code=1)["error"]["code"], "HTTP_ERROR")
        self.assertEqual(self.cli("publish", "sample", "--version", "2.0.0", code=2)["error"]["code"], "BAD_USAGE")
        self.backend.reject_refresh = True
        self.assertEqual(self.cli("status", code=4)["error"]["code"], "UNAUTHORIZED")
        self.assertFalse((self.state / "auth.json").exists())

    def test_foreign_submissions_are_rejected_without_exposing_rows(self):
        self.backend.foreign_status = True
        response = self.cli("status", code=1)
        self.assertEqual(response["error"]["code"], "BACKEND_RESPONSE_INVALID")

    def test_offline_and_recoverable_backend_failure_preserve_mirror_use(self):
        before = len(self.backend.calls)
        result = self.cli("search", "demo", "--offline")
        self.assertEqual(result["source"], "mirror")
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(self.mirror.requests, [])
        result = self.cli("search", "demo")
        self.assertEqual(result["source"], "mirror")
        self.assertTrue(any(c["path"].startswith("/community/skills?") for c in self.backend.calls))
        installed = self.cli("install", "demo-search", "--agent", "claude")
        self.assertEqual(installed["source"], "mirror")
        self.assertTrue((self.root / ".claude/skills/demo-search/SKILL.md").is_file())
        self.assertFalse(any(c["path"].endswith("/install") or c["path"].startswith("/events/") for c in self.backend.calls))

    def test_configured_telemetry_is_a_dedicated_authenticated_event(self):
        self.config["telemetry_endpoint"] = "/events/installations"
        (self.state / "config.json").write_text(json.dumps(self.config))
        (self.state / 'index_cache.json').write_text(json.dumps({
            'mirror_repo_url': self.mirror_url, 'fetched_at': time.time(),
            'index': {'schema_version': 1, 'skills': [
                {'dir': 'sample', 'community_id': 'skill-one', 'latest_version': '1.0.0',
                 'versions': [{'version': '1.0.0', 'commit': 'abcdef12'}]},
            ]},
        }))
        self.backend.event_gate = threading.Event()
        command = ('import sys; sys.path.insert(0, sys.argv[1]); import telemetry, store; '
                   'from cli.context import CliContext; '
                   'ctx=CliContext(config=store.load_config(),auth={**store.load_auth(),"access_token":"synthetic-flow-access"}); '
                   'telemetry.report("install", {"skill":"sample","version":"1.0.0","ts":1.0,"private":"never-send"},ctx=ctx)')
        try:
            result = subprocess.run([sys.executable, '-c', command, str(SCRIPTS)],
                                    env={**self.env, 'LEARNOVA_TELEMETRY': '1'},
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
            self.assertTrue(self.backend.event_started.wait(5))
            self.assertFalse(self.backend.event.is_set())
        finally:
            self.backend.event_gate.set()
        with patch.dict(os.environ, {"LEARNOVA_TELEMETRY": "1"}):
            self.assertTrue(self.backend.event.wait(5), "Configured fixture event did not arrive")
        call = next(c for c in self.backend.calls if urlsplit(c["path"]).path == "/events/installations")
        self.assertEqual(parse_qs(urlsplit(call['path']).query), {'community_id': ['skill-one']})
        self.assertEqual(json.loads(call["body"]), {"event": "install", "skill": "sample", "version": "1.0.0", "ts": 1.0})
        uuid.UUID(call["headers"]["X-Nonce"])
        self.assertIn("Authorization", call["headers"])
        self.assertEqual(self.backend.refresh_count, 0)
        self.assertFalse(any(c["path"].endswith("/install") for c in self.backend.calls))


if __name__ == "__main__":
    unittest.main()
