"""Telemetry is optional, bounded, and isolated from the install result."""

import os
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/learnova-cli/scripts"
sys.path.insert(0, str(SCRIPTS))
import auth
from cli.commands import install
from cli.context import CliContext
from core.errors import AuthError, CliError
import store
import telemetry


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.config = {**store.DEFAULTS, "backend_url": "https://example.invalid", "telemetry_endpoint": "/events/installs"}
        self.credentials = {"backend_origin": "https://example.invalid:443", "refresh_token": "synthetic-refresh"}
        self.payload = {"skill": "sample", "version": "1.0.0", "ts": 1.0, "private": "never-send"}
        self.ctx = CliContext(config=self.config, auth={**self.credentials, "access_token": "synthetic-access"})
        self.catalog = {'schema_version': 1, 'generated_at': 0, 'skills': [
            {'dir': 'sample', 'community_id': 'community-one', 'latest_version': '1.0.0',
             'versions': [{'version': '1.0.0', 'commit': 'abcdef12'}]},
        ]}
        cache = patch.object(store, 'load_index_cache', side_effect=lambda *a: (self.catalog, False))
        cache.start()
        self.addCleanup(cache.stop)
        for name, value in (("load_config", self.config), ("load_auth", self.credentials)):
            mocked = patch.object(store, name, side_effect=lambda value=value: dict(value))
            mocked.start()
            self.addCleanup(mocked.stop)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_configured_install_sends_only_allowed_fields_with_short_timeout(self):
        with patch.object(auth, "request") as request:
            telemetry._do_send("install", self.payload, self.ctx)
        ctx, method, path = request.call_args.args
        self.assertTrue(auth.has_credentials(ctx))
        self.assertEqual((method, path), ("POST", "/events/installs?community_id=community-one"))
        self.assertEqual(request.call_args.kwargs, {"body": {"event": "install", "skill": "sample", "version": "1.0.0", "ts": 1.0}, "timeout": 2, "allow_refresh": False})

    def test_environment_off_config_off_and_logged_out_never_send(self):
        for mode in ("env", "config", "logout"):
            with self.subTest(mode=mode), patch.object(auth, "request") as request:
                if mode == "env":
                    with patch.dict(os.environ, {"LEARNOVA_TELEMETRY": "0"}):
                        telemetry._do_send("install", self.payload, self.ctx)
                elif mode == "config":
                    self.config["telemetry"] = "off"
                    telemetry._do_send("install", self.payload, self.ctx)
                    self.config["telemetry"] = "auto"
                else:
                    self.credentials.clear()
                    telemetry._do_send("install", self.payload, self.ctx)
                request.assert_not_called()

    def test_unconfigured_endpoint_other_event_and_changed_origin_do_not_send(self):
        with patch.object(auth, "request") as request:
            telemetry._do_send("search", self.payload, self.ctx)
            self.config["telemetry_endpoint"] = ""
            telemetry._do_send("install", self.payload, self.ctx)
            self.config["telemetry_endpoint"] = "/events/installs"
            self.credentials["backend_origin"] = "https://other.invalid:443"
            telemetry._do_send("install", self.payload, self.ctx)
        request.assert_not_called()

    def test_invalid_payload_and_send_failures_are_silent(self):
        with patch.object(auth, "request", side_effect=RuntimeError("synthetic failure")) as request:
            for changes in ({"skill": "../outside"}, {"ts": True}, {"ts": -1}, {"ts": float("nan")}, {"version": ""}):
                telemetry._send("install", {**self.payload, **changes}, self.ctx)
            request.assert_not_called()
            telemetry._send("install", self.payload, self.ctx)
            request.assert_called_once()

    def test_worker_creation_and_configuration_errors_never_escape(self):
        with patch.object(telemetry.subprocess, "Popen", side_effect=OSError("worker unavailable")):
            telemetry.report("install", self.payload, ctx=self.ctx)
        with patch.object(store, "load_config", side_effect=RuntimeError("invalid state")):
            telemetry.report("install", self.payload, ctx=self.ctx)

    def test_worker_receives_only_public_event_fields_and_no_output_handles(self):
        with patch.object(telemetry.subprocess, "Popen") as spawn:
            telemetry.report("install", self.payload, ctx=self.ctx)
            command = spawn.call_args.args[0]
            options = spawn.call_args.kwargs
            self.payload["skill"] = "changed"
            message = json.loads(spawn.return_value.stdin.write.call_args.args[0])
            self.assertEqual(message['payload'], {"skill": "sample", "version": "1.0.0", "ts": 1.0})
            self.assertNotIn('synthetic-access', repr(command))
            self.assertNotIn('synthetic-access', repr(options['env']))
            spawn.return_value.stdin.close.assert_called_once()
            self.assertEqual(options["stdout"], telemetry.subprocess.DEVNULL)
            self.assertEqual(options["stderr"], telemetry.subprocess.DEVNULL)
            spawn.assert_called_once()

    def test_background_unauthorized_preserves_foreground_rotated_credentials(self):
        self.credentials['refresh_token'] = 'synthetic-new-foreground-token'
        with patch.object(auth.net, 'request', side_effect=AuthError('UNAUTHORIZED', 'expired')), \
             patch.object(auth, '_refresh') as refresh, patch.object(store, 'clear_auth') as clear:
            telemetry._send('install', self.payload, self.ctx)
        refresh.assert_not_called()
        clear.assert_not_called()
        self.assertEqual(self.credentials['refresh_token'], 'synthetic-new-foreground-token')

    def test_missing_ambiguous_or_unmatched_mapping_never_sends_or_fetches(self):
        with patch.object(auth, 'request') as request, patch.object(auth.net, 'request') as network:
            for changed in (None, {'schema_version': 1, 'skills': []},
                            {**self.catalog, 'skills': self.catalog['skills'] * 2}):
                with patch.object(store, 'load_index_cache', return_value=(changed, True)):
                    telemetry._send('install', self.payload, self.ctx)
            telemetry._send('install', {**self.payload, 'version': '9.9.9'}, self.ctx)
        request.assert_not_called()
        network.assert_not_called()

    def test_install_reports_after_success_and_never_after_failed_commit(self):
        args = type("Args", (), {"skill": "sample", "agent": None, "project": False, "force": False})()
        installed = type("Installed", (), {})()
        installed.version = "1.0.0"
        with patch.object(install.agents, "resolve_targets", return_value=[]), \
             patch.object(install.resolver, "resolve"), patch.object(install.engine, "fetch_and_install", return_value=[installed]) as engine, \
             patch.object(telemetry, "report") as report:
            result = install.handler(args, CliContext())
            self.assertEqual(result["version"], "1.0.0")
            report.assert_called_once()
            report.reset_mock()
            engine.side_effect = CliError("SKILL_PACKAGE_INVALID", "Synthetic failure")
            with self.assertRaises(CliError):
                install.handler(args, CliContext())
            report.assert_not_called()


if __name__ == "__main__":
    unittest.main()
