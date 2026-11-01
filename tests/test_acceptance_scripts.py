"""Acceptance safety gates and fixture cleanup, without external services."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import mirror_smoke
import t2_backend_flow as backend_flow
from fixtures.gitlab_stub import MirrorStub


class AcceptanceScriptsTests(unittest.TestCase):
    def test_real_service_scripts_require_opt_in(self):
        for script in (mirror_smoke, backend_flow):
            with self.subTest(script=script.__name__), patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
                self.assertEqual(script.main(), 2)

    def test_backend_missing_credentials_is_not_success(self):
        with patch.dict(os.environ, {"LEARNOVA_RUN_BACKEND_TESTS": "1"}, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual(backend_flow.main(), 2)

    def test_mirror_smoke_uses_dynamic_fixture_and_isolated_home(self):
        stub = MirrorStub(Path(__file__).parent / "fixtures/mirror")
        url = stub.start()
        self.addCleanup(stub.stop)
        result = mirror_smoke.run(url + "/demo/mirror")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["catalog_total"], 2)
        self.assertTrue(result["cleanup"])

    def test_backend_default_does_not_publish_or_write_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "sample"
            folder.mkdir()
            (folder / "SKILL.md").write_text("synthetic", encoding="utf-8")
            source = Mock()
            source.search.return_value = {"source": "backend", "total": 1, "skills": [{"dir": "sample"}]}
            source.info.return_value = {"dir": "sample", "versions": [{"version": "1.0.0"}]}
            source.fetch.return_value = folder
            with patch.object(backend_flow.auth, "login", side_effect=lambda ctx, *args: ctx.auth.update(account={"uuid": "actor"}) or {"uuid": "actor"}), \
                    patch.object(backend_flow.auth, "whoami", return_value={"logged_in": True}), \
                    patch.object(backend_flow.auth, "logout", return_value={"ok": True}), \
                    patch.object(backend_flow, "BackendSource", return_value=source), \
                    patch.object(backend_flow, "read_install_metadata", return_value={"version": "1.0.0"}), \
                    patch.object(backend_flow.publishing, "status", return_value={"source": "backend", "submissions": []}), \
                    patch.object(backend_flow, "publish_fixture") as publish:
                result = backend_flow.run("https://api.example.invalid", "test@example.invalid", "synthetic-password")
                self.assertEqual(result["mode"], "read_only")
                publish.assert_not_called()

    def test_cleanup_refuses_nonmatching_remote_resource(self):
        ctx = SimpleNamespace(auth={"account": {"uuid": "actor"}})
        real_request = Mock(return_value={"name": "unrelated", "user_uuid": "actor"})

        def publish(context, folder, version, changelog):
            backend_flow.auth.request(context, "POST", "/library/skills/upload")
            raise RuntimeError("synthetic failure after upload")

        def request(context, method, path, *args, **kwargs):
            if method == "POST":
                return {"id": "new-library", "user_uuid": "actor", "community_skill_id": None}
            return real_request(context, method, path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp, patch.object(backend_flow.auth, "request", request), \
                patch.object(backend_flow.publishing, "publish", side_effect=publish):
            with self.assertRaises(RuntimeError):
                backend_flow.publish_fixture(ctx, Path(temp))
        self.assertEqual([call.args[1] for call in real_request.call_args_list], ["GET"])

    def test_write_fixture_cleans_only_created_owned_ids(self):
        ctx = SimpleNamespace(auth={"account": {"uuid": "actor"}})
        calls, fixture_name = [], []

        def request(context, method, path, *args, **kwargs):
            calls.append((method, path))
            if path == "/library/skills/upload":
                return {"id": "created-library", "user_uuid": "actor", "community_skill_id": None}
            if path.endswith("/publish"):
                return {"skill_id": "created-community", "submitted_by": "actor"}
            return {"name": fixture_name[0], "user_uuid": "actor", "owner_uuid": "actor"}

        def publish(context, folder, version, changelog):
            fixture_name.append(folder.name)
            backend_flow.auth.request(context, "POST", "/library/skills/upload")
            backend_flow.auth.request(context, "POST", "/library/skills/created-library/publish")
            return {"submitted": True, "status": "PENDING_OWNER", "skill_id": "created-community", "version_id": "created-version"}

        with tempfile.TemporaryDirectory() as temp, patch.object(backend_flow.auth, "request", request), \
                patch.object(backend_flow.publishing, "publish", side_effect=publish), \
                patch.object(backend_flow.publishing, "status", return_value={"submissions": [{"version_id": "created-version"}]}):
            self.assertTrue(backend_flow.publish_fixture(ctx, Path(temp))["fixture_cleanup"])
        self.assertEqual([path for method, path in calls if method == "DELETE"],
                         ["/community/skills/created-community", "/library/skills/created-library"])

    def test_cleanup_failure_is_visible_without_exception_text(self):
        environment = {"LEARNOVA_RUN_BACKEND_TESTS": "1", "LEARNOVA_TEST_BACKEND_URL": "https://api.example.invalid",
                       "LEARNOVA_TEST_EMAIL": "test@example.invalid", "LEARNOVA_TEST_PASSWORD": "synthetic"}
        with patch.dict(os.environ, environment, clear=True), redirect_stdout(io.StringIO()) as output, \
                patch.object(backend_flow, "run", side_effect=backend_flow.FixtureCleanupError("synthetic-secret")):
            self.assertEqual(backend_flow.main(), 1)
        self.assertIn("FIXTURE_CLEANUP_FAILED", output.getvalue())
        self.assertNotIn("synthetic-secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
