"""Account flow tests use only synthetic credentials, temporary files and localhost."""

import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import warnings

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/learnova-cli/scripts"
sys.path.insert(0, str(SCRIPTS))
import auth
import bootstrap
from cli.context import CliContext
from cli.commands import init, login, logout, whoami
from core.errors import AuthError, CliError
import net
import store

ACCESS = "synthetic-access-token"
REFRESH = "synthetic-refresh-token"
PASSWORD = "synthetic-password"


class AuthFixture:
    def __init__(self):
        self.calls = []
        self.refresh_count = 0
        self.expire_access = False
        self.reject_refresh = False
        self.reject_me = False
        self.invalid_account = False
        self.missing_cookie = False
        self.redirect = None
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, value=None, status=200, cookies=()):
                raw = value if isinstance(value, bytes) else (json.dumps(value).encode() if value is not None else b"")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for cookie in cookies:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(raw)

            def record(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                fixture.calls.append({"method": self.command, "path": self.path,
                                      "headers": dict(self.headers), "body": raw})
                return raw

            def do_POST(self):
                self.record()
                if self.path == "/auth/login":
                    cookies = [] if fixture.missing_cookie else ["unrelated=value; Path=/", f"refresh_token={REFRESH}; HttpOnly; Path=/auth"]
                    self.send({"access_token": ACCESS}, cookies=cookies)
                elif self.path == "/auth/refresh":
                    fixture.refresh_count += 1
                    if fixture.reject_refresh:
                        self.send({"detail": PASSWORD}, 401)
                    else:
                        self.send({"access_token": ACCESS + str(fixture.refresh_count)},
                                  cookies=[f"refresh_token={REFRESH}{fixture.refresh_count}; HttpOnly; Path=/auth"])
                elif self.path == "/auth/logout":
                    self.send(status=204)
                else:
                    self.send({"ok": True})

            def do_PUT(self):
                self.record()
                self.send(status=204)

            def do_GET(self):
                self.record()
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", fixture.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path in {"/auth/me", "/blob"}:
                    bearer = self.headers.get("Authorization")
                    if fixture.reject_me or (fixture.expire_access and bearer == "Bearer " + ACCESS):
                        self.send({"detail": PASSWORD}, 401)
                    elif self.path == "/blob":
                        self.send(b"\x00\xffsynthetic-binary")
                    elif fixture.invalid_account:
                        self.send({"access_token": ACCESS})
                    else:
                        self.send({"username": "demo-user", "uuid": "demo-id", "email": "demo@example.invalid",
                                   "role": "user", "password": PASSWORD, "access_token": ACCESS})
                elif self.path == "/error":
                    self.send({"detail": PASSWORD}, 503)
                else:
                    self.send({"ok": True})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.fixture = AuthFixture()
        self.addCleanup(self.fixture.close)
        self.ctx = CliContext(config={**store.DEFAULTS, "backend_url": self.fixture.url}, cwd=self.root)
        for name in ("AUTH_PATH", "CONFIG_PATH"):
            patcher = patch.object(store, name, self.root / name.lower())
            patcher.start()
            self.addCleanup(patcher.stop)

    def login(self):
        return auth.login(self.ctx, "demo@example.invalid", PASSWORD)

    def test_cookie_login_and_redacted_account(self):
        account = self.login()
        self.assertEqual(account["username"], "demo-user")
        self.assertEqual(set(account), {"username", "email", "role", "uuid"})
        self.assertNotIn(PASSWORD, json.dumps(account))
        self.assertNotIn(ACCESS, json.dumps(account))
        stored = store.load_auth()
        self.assertEqual(stored["refresh_token"], REFRESH)
        self.assertNotIn("access_token", stored)
        self.assertNotIn(PASSWORD, store.AUTH_PATH.read_text())
        self.assertEqual(store.AUTH_PATH.stat().st_mode & 0o777, 0o600)
        self.assertTrue(auth.has_credentials(self.ctx))
        me = self.fixture.calls[1]
        self.assertEqual(me["headers"].get("Authorization"), "Bearer " + ACCESS)
        self.assertNotIn("Cookie", me["headers"])
        self.assertNotIn("X-Nonce", self.fixture.calls[0]["headers"])

    def test_new_process_refreshes_cookie_and_persists_rotation(self):
        self.login()
        self.ctx.auth = store.load_auth()
        self.assertEqual(auth.current_user(self.ctx)["username"], "demo-user")
        self.assertEqual(self.fixture.refresh_count, 1)
        refresh = self.fixture.calls[-2]
        self.assertEqual(refresh["path"], "/auth/refresh")
        self.assertEqual(refresh["headers"]["Cookie"], "refresh_token=" + REFRESH)
        self.assertNotIn("Authorization", refresh["headers"])
        self.assertEqual(store.load_auth()["refresh_token"], REFRESH + "1")
        self.assertNotIn("access_token", store.load_auth())

    def test_expired_access_retries_with_rotated_refresh(self):
        self.login()
        self.fixture.expire_access = True
        self.assertEqual(auth.current_user(self.ctx)["username"], "demo-user")
        self.assertEqual(self.fixture.refresh_count, 1)
        self.assertEqual([call["path"] for call in self.fixture.calls[-3:]],
                         ["/auth/me", "/auth/refresh", "/auth/me"])

    def test_binary_download_refreshes_access_and_keeps_bearer_on_origin(self):
        self.login()
        self.fixture.expire_access = True
        self.assertEqual(auth.request(self.ctx, "GET", "/blob", binary=True), b"\x00\xffsynthetic-binary")
        self.assertEqual(self.fixture.refresh_count, 1)
        self.assertEqual([call["path"] for call in self.fixture.calls[-3:]],
                         ["/blob", "/auth/refresh", "/blob"])
        self.assertNotIn("Cookie", self.fixture.calls[-1]["headers"])
        with self.assertRaises(CliError) as raised:
            auth.request(self.ctx, "GET", "/blob", binary=True, max_bytes=1)
        self.assertEqual(raised.exception.code, "HTTP_RESPONSE_TOO_LARGE")

    def test_binary_download_never_redirects_bearer_to_another_origin(self):
        self.login()
        other = AuthFixture()
        self.addCleanup(other.close)
        self.fixture.redirect = other.url + "/capture"
        with self.assertRaises(CliError) as raised:
            auth.request(self.ctx, "GET", "/redirect", binary=True)
        self.assertEqual(raised.exception.http_status, 302)
        self.assertEqual(other.calls, [])

    def test_rejected_refresh_clears_local_credentials(self):
        self.login()
        self.ctx.auth = store.load_auth()
        self.fixture.reject_refresh = True
        with self.assertRaises(AuthError) as raised:
            auth.current_user(self.ctx)
        self.assertEqual(raised.exception.exit_code, 4)
        self.assertNotIn(PASSWORD, raised.exception.message)
        self.assertFalse(store.AUTH_PATH.exists())
        self.assertFalse(self.ctx.auth)

    def test_invalid_access_after_refresh_is_not_retried_again(self):
        self.login()
        self.fixture.reject_me = True
        with self.assertRaises(AuthError):
            auth.current_user(self.ctx)
        self.assertEqual(self.fixture.refresh_count, 1)
        self.assertFalse(store.AUTH_PATH.exists())

    def test_missing_refresh_cookie_is_invalid(self):
        self.fixture.missing_cookie = True
        with self.assertRaises(CliError) as raised:
            self.login()
        self.assertEqual(raised.exception.code, "BACKEND_RESPONSE_INVALID")
        self.assertFalse(store.AUTH_PATH.exists())

    def test_invalid_account_is_sanitized(self):
        self.fixture.invalid_account = True
        with self.assertRaises(CliError) as raised:
            self.login()
        self.assertEqual(raised.exception.code, "BACKEND_RESPONSE_INVALID")
        self.assertNotIn(ACCESS, raised.exception.message)

    def test_origin_binding_prevents_old_credentials_reaching_new_server(self):
        self.login()
        other = AuthFixture()
        self.addCleanup(other.close)
        self.ctx.config["backend_url"] = other.url
        self.assertFalse(auth.has_credentials(self.ctx))
        self.assertEqual(whoami.handler(None, self.ctx), {"logged_in": False, "username": None})
        with self.assertRaises(AuthError):
            auth.request(self.ctx, "GET", "/auth/me")
        logout.handler(None, self.ctx)
        self.assertEqual(other.calls, [])
        self.assertFalse(store.AUTH_PATH.exists())

    def test_unbound_legacy_credentials_are_not_sent(self):
        self.ctx.auth = {"refresh_token": REFRESH, "access_token": ACCESS}
        with self.assertRaises(AuthError):
            auth.request(self.ctx, "GET", "/auth/me")
        self.assertEqual(self.fixture.calls, [])

    def test_logout_revokes_cookie_and_clears_state(self):
        self.login()
        result = logout.handler(None, self.ctx)
        self.assertEqual(result, {"ok": True, "message": "已退出登录"})
        self.assertEqual(self.fixture.calls[-1]["path"], "/auth/logout")
        self.assertEqual(self.fixture.calls[-1]["headers"]["Cookie"], "refresh_token=" + REFRESH)
        self.assertFalse(store.AUTH_PATH.exists())
        self.assertEqual(whoami.handler(None, self.ctx), {"logged_in": False, "username": None})

    def test_logout_clears_state_when_remote_is_unreachable(self):
        self.login()
        with patch.object(net, "request", side_effect=CliError("UNREACHABLE", "服务不可达")):
            self.assertTrue(auth.logout(self.ctx)["ok"])
        self.assertFalse(store.AUTH_PATH.exists())

    def test_authenticated_write_has_nonce_and_raw_content_type(self):
        self.login()
        result = auth.request(self.ctx, "PUT", "/repository", data=b"raw archive", content_type="application/zip")
        self.assertIsNone(result)
        call = self.fixture.calls[-1]
        self.assertEqual(call["body"], b"raw archive")
        self.assertEqual(call["headers"]["Content-Type"], "application/zip")
        self.assertEqual(str(uuid.UUID(call["headers"]["X-Nonce"])), call["headers"]["X-Nonce"])
        float(call["headers"]["X-Nonce-Timestamp"])
        self.assertNotIn("Cookie", call["headers"])

    def test_invalid_base_urls_fail_without_disclosing_input(self):
        for value in ("https://demo:" + PASSWORD + "@example.invalid", "https://@example.invalid",
                      "https://example.invalid?token=" + PASSWORD, "https://example.invalid/#" + PASSWORD,
                      "file:///tmp/demo", "https://example.invalid:invalid", "https://example.invalid/\n", ["invalid"]):
            with self.subTest(kind=type(value).__name__):
                self.ctx.config["backend_url"] = value
                with self.assertRaises(CliError) as raised:
                    auth.backend_url(self.ctx)
                self.assertEqual(raised.exception.code, "BAD_USAGE")
                self.assertNotIn(PASSWORD, raised.exception.message)
        self.assertEqual(self.fixture.calls, [])

    def test_unconfigured_backend_has_actionable_error(self):
        self.ctx.config["backend_url"] = ""
        with self.assertRaises(CliError) as raised:
            auth.login(self.ctx, "demo@example.invalid", PASSWORD)
        self.assertEqual(raised.exception.code, "BACKEND_NOT_CONFIGURED")

    def test_authenticated_request_rejects_external_and_traversing_paths(self):
        self.login()
        before = len(self.fixture.calls)
        for path in ("https://example.invalid/me", "//example.invalid/me", "/../me", "/%2e%2e/me", "/me#fragment", "/me\\x"):
            with self.subTest(path=path), self.assertRaises(CliError):
                auth.request(self.ctx, "GET", path)
        self.assertEqual(before, len(self.fixture.calls))

    def test_redirect_never_sends_cookie_or_bearer_to_another_origin(self):
        other = AuthFixture()
        self.addCleanup(other.close)
        self.fixture.redirect = other.url + "/capture"
        for headers in ({"Cookie": "refresh_token=" + REFRESH}, {"Authorization": "Bearer " + ACCESS}):
            with self.subTest(header=list(headers)), self.assertRaises(CliError) as raised:
                net.request("GET", self.fixture.url + "/redirect", headers=headers)
            self.assertEqual(raised.exception.http_status, 302)
            self.assertNotIn(REFRESH, raised.exception.message)
            self.assertNotIn(ACCESS, raised.exception.message)
        self.assertEqual(other.calls, [])

    def test_same_origin_cookie_redirect_is_allowed(self):
        self.fixture.redirect = self.fixture.url + "/target"
        self.assertTrue(net.request("GET", self.fixture.url + "/redirect", headers={"Cookie": "refresh_token=" + REFRESH})["ok"])
        self.assertEqual(self.fixture.calls[-1]["headers"]["Cookie"], "refresh_token=" + REFRESH)

    def test_http_error_preserves_status_and_redacts_body(self):
        with self.assertRaises(CliError) as raised:
            net.request("GET", self.fixture.url + "/error")
        self.assertEqual(raised.exception.http_status, 503)
        self.assertNotIn(PASSWORD, raised.exception.message)

    def test_bad_headers_and_content_type_are_rejected_before_request(self):
        for headers in ({"X-Test": "unsafe\r\nCookie: " + REFRESH}, {"Host": "example.invalid"}, {"bad header": "x"}):
            with self.subTest(headers=list(headers)), self.assertRaises(CliError) as raised:
                net.request("GET", self.fixture.url, headers=headers)
            self.assertEqual(raised.exception.code, "BAD_USAGE")
        with self.assertRaises(CliError):
            net.request("POST", self.fixture.url, data=b"a", content_type="unsafe\r\nX-Test: a")
        self.assertEqual(self.fixture.calls, [])

    def test_login_refuses_nonterminal_without_reading_or_network(self):
        with patch.object(auth, "terminal_available", return_value=False), patch("builtins.input") as input_mock, patch.object(auth.getpass, "getpass") as password_mock:
            with self.assertRaises(CliError) as raised:
                login.handler(None, self.ctx)
        self.assertEqual(raised.exception.code, "BAD_USAGE")
        input_mock.assert_not_called()
        password_mock.assert_not_called()
        self.assertEqual(self.fixture.calls, [])

    def test_interactive_login_uses_hidden_password_prompt(self):
        with patch.object(auth, "terminal_available", return_value=True), patch("builtins.input", return_value="demo@example.invalid"), patch.object(auth.getpass, "getpass", return_value=PASSWORD):
            result = login.handler(None, self.ctx)
        self.assertTrue(result["logged_in"])
        self.assertNotIn(PASSWORD, json.dumps(result))
        self.assertNotIn(REFRESH, json.dumps(result))

    def test_password_prompt_cannot_fall_back_to_echo(self):
        def insecure_prompt(*args):
            warnings.warn("synthetic terminal failure", getpass_warning)
            self.fail("password entry must stop before echo fallback")
        getpass_warning = auth.getpass.GetPassWarning
        with patch.object(auth, "terminal_available", return_value=True), patch("builtins.input", return_value="demo@example.invalid"), patch.object(auth.getpass, "getpass", side_effect=insecure_prompt):
            with self.assertRaises(CliError) as raised:
                auth.login_interactive(self.ctx)
        self.assertEqual(raised.exception.code, "BAD_USAGE")
        self.assertEqual(self.fixture.calls, [])

    def test_login_cli_rejects_password_arguments_and_does_not_read_environment(self):
        env = {**os.environ, "LEARNOVA_HOME": str(self.root / "subprocess"), "HOME": str(self.root),
               "LEARNOVA_PASSWORD": PASSWORD}
        for args in (["login"], ["login", "--password", PASSWORD],
                     ["login", "demo@example.invalid", PASSWORD]):
            with self.subTest(argument_count=len(args)):
                result = subprocess.run([sys.executable, str(SCRIPTS / "learnova.py"), "--json", *args], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], "BAD_USAGE")
                self.assertNotIn(PASSWORD, result.stdout + result.stderr)
                self.assertNotIn("demo@example.invalid", result.stdout + result.stderr)


class InitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.ctx = CliContext(config=dict(store.DEFAULTS), cwd=self.root)
        self.path_patch = patch.object(store, "CONFIG_PATH", self.root / ".learnova/config.json")
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.home_patch = patch.object(Path, "home", return_value=self.root)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def test_noninteractive_preview_does_not_save(self):
        (self.root / ".codex").mkdir()
        with patch.object(auth, "terminal_available", return_value=False), patch("builtins.input") as input_mock:
            result = init.handler(None, self.ctx)
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["kind"], "configuration")
        self.assertEqual(result["detected_agents"], ["codex"])
        self.assertTrue(result["questions"])
        self.assertFalse(store.CONFIG_PATH.exists())
        input_mock.assert_not_called()

    def test_confirmed_initialization_is_idempotent(self):
        (self.root / ".codex").mkdir()
        with patch.object(auth, "terminal_available", return_value=True), patch("builtins.input", return_value="yes") as input_mock:
            first = bootstrap.initialize(self.ctx)
        self.assertEqual(first["status"], "completed")
        input_mock.assert_called_once()
        content = store.CONFIG_PATH.read_bytes()
        self.assertEqual(store.CONFIG_PATH.stat().st_mode & 0o777, 0o600)
        with patch("builtins.input") as input_mock:
            second = bootstrap.initialize(self.ctx)
        self.assertEqual(first, second)
        self.assertEqual(content, store.CONFIG_PATH.read_bytes())
        input_mock.assert_not_called()

    def test_declined_preview_does_not_save(self):
        (self.root / ".claude").mkdir()
        with patch.object(auth, "terminal_available", return_value=True), patch("builtins.input", return_value="no"):
            result = bootstrap.initialize(self.ctx)
        self.assertEqual(result["status"], "needs_input")
        self.assertFalse(store.CONFIG_PATH.exists())

    def test_existing_explicit_configuration_is_preserved(self):
        configured = {"agents": ["claude"], "install_scope": "project", "telemetry": "off"}
        store._write_json(store.CONFIG_PATH, configured)
        self.ctx.config = {**store.DEFAULTS, **configured}
        before = store.CONFIG_PATH.read_bytes()
        with patch("builtins.input") as input_mock:
            result = bootstrap.initialize(self.ctx)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["config"]["agents"], ["claude"])
        self.assertEqual(result["targets"][0]["path"], str(self.root / ".claude/skills"))
        self.assertEqual(before, store.CONFIG_PATH.read_bytes())
        input_mock.assert_not_called()

    def test_no_agents_returns_actionable_question_without_side_effects(self):
        with patch("builtins.input") as input_mock:
            result = bootstrap.initialize(self.ctx)
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["questions"][0]["key"], "agents")
        self.assertFalse(store.CONFIG_PATH.exists())
        input_mock.assert_not_called()

    def test_configuration_preview_never_displays_url_credentials(self):
        (self.root / ".codex").mkdir()
        for key in ("backend_url", "mirror_repo_url"):
            with self.subTest(key=key):
                self.ctx.config = {**store.DEFAULTS, key: "https://demo:" + PASSWORD + "@example.invalid"}
                with self.assertRaises(CliError) as raised, patch("builtins.input") as input_mock:
                    bootstrap.initialize(self.ctx)
                self.assertNotIn(PASSWORD, raised.exception.message)
                input_mock.assert_not_called()
        self.assertFalse(store.CONFIG_PATH.exists())


if __name__ == "__main__":
    unittest.main()
