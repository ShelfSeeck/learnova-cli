"""Opt-in backend acceptance. Default operations only read community/account content.

CI uses a dedicated test account, never interactive CLI login. Secrets stay in memory;
ordinary users must run the terminal-only CLI login themselves.
"""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import quote
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/learnova-cli/scripts"))
import auth
from cli.context import CliContext
from core.errors import CliError
from core.skill import read_install_metadata
import publishing
from sources.backend import BackendSource
import store


class FixtureCleanupError(RuntimeError):
    pass


def _check(condition):
    if not condition:
        raise RuntimeError("backend acceptance assertion failed")


def _id(value):
    _check(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value))
    return quote(value, safe="")


def publish_fixture(ctx, root):
    """Only remove exact IDs created by this run, after name and ownership checks."""
    name = "ci-smoke-" + uuid.uuid4().hex
    folder = root / name
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: " + name + "\ndescription: Synthetic queue reference.\n---\n"
                                    "A queue stores items in arrival order.\n", encoding="utf-8")
    actor = ctx.auth["account"]["uuid"]
    request = auth.request
    libraries, communities = set(), set()

    def observed(context, method, path, *args, **kwargs):
        response = request(context, method, path, *args, **kwargs)
        if isinstance(response, dict):
            if method == "POST" and path == "/library/skills/upload":
                _check(response.get("user_uuid") == actor and response.get("community_skill_id") is None)
                libraries.add(_id(response.get("id")))
            elif method == "POST" and path.endswith("/publish"):
                _check(response.get("submitted_by") == actor)
                communities.add(_id(response.get("skill_id")))
        return response

    cleanup_failed = False
    try:
        with patch.object(auth, "request", observed):
            result = publishing.publish(ctx, folder, "1.0.0", "Synthetic integration validation.")
        state = publishing.status(ctx, result["skill_id"])
        _check(result["submitted"] and result["status"] in {"PENDING_OWNER", "PENDING_ADMIN"})
        _check(any(item["version_id"] == result["version_id"] for item in state["submissions"]))
    finally:
        for identity in sorted(communities):
            try:
                path = "/community/skills/" + identity
                record = request(ctx, "GET", path)
                _check(record.get("name") == name and record.get("owner_uuid") == actor)
                request(ctx, "DELETE", path)
            except Exception:
                cleanup_failed = True
        for identity in sorted(libraries):
            try:
                path = "/library/skills/" + identity
                record = request(ctx, "GET", path)
                _check(record.get("name") == name and record.get("user_uuid") == actor)
                request(ctx, "DELETE", path)
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise FixtureCleanupError("synthetic fixture cleanup requires operator attention") from None
    return {"publish": "pending_review", "fixture_cleanup": True}


def run(base, email, password, *, mirror_url=None, allow_writes=False):
    with tempfile.TemporaryDirectory(prefix="learnova-t2-") as temp:
        root = Path(temp)
        config = {**store.DEFAULTS, "backend_url": auth.validate_base_url(base), "telemetry": "off"}
        if mirror_url:
            config["mirror_repo_url"] = auth.validate_base_url(mirror_url)
        ctx = CliContext(config=config, cwd=root)
        # Do not persist login credentials, even to temporary files.
        with patch.object(store, "save_auth"), patch.object(store, "clear_auth"), \
                patch.object(store, "CONFIG_PATH", root / "config.json"), \
                patch.object(store, "INDEX_CACHE_PATH", root / "index_cache.json"):
            try:
                account = auth.login(ctx, email, password)
                password = None
                _check(bool(account.get("uuid")))
                _check(auth.whoami(ctx).get("logged_in") is True)
                source = BackendSource(ctx)
                search = source.search("", [], "popular")
                _check(search.get("source") == "backend" and search.get("total", 0) > 0)
                detail = source.info(search["skills"][0]["dir"])
                _check(bool(detail.get("versions")))
                selected = min(detail["versions"], key=lambda item: item.get("size_bytes", 0))["version"]
                package = source.fetch(detail["dir"], selected, root / "downloads")
                _check((package / "SKILL.md").is_file() and read_install_metadata(package).get("version") == selected)
                state = publishing.status(ctx)
                _check(state.get("source") == "backend" and isinstance(state.get("submissions"), list))
                result = {"status": "passed", "mode": "write_fixture" if allow_writes else "read_only",
                          "login": True, "online_search": True, "detail": True, "pinned_fetch": True,
                          "review_status": True, "telemetry": "not_exercised"}
                if allow_writes:
                    result.update(publish_fixture(ctx, root))
                return result
            finally:
                if ctx.auth:
                    _check(auth.logout(ctx).get("ok") is True)


def main():
    if os.environ.get("LEARNOVA_RUN_BACKEND_TESTS") != "1":
        print(json.dumps({"status": "not_run", "reason": "explicit backend opt-in required"}))
        return 2
    email = os.environ.get("LEARNOVA_TEST_EMAIL") or os.environ.get("LEARNOVA_TEST_USERNAME")
    password = os.environ.get("LEARNOVA_TEST_PASSWORD")
    base = os.environ.get("LEARNOVA_TEST_BACKEND_URL")
    if not all((base, email, password)):
        print(json.dumps({"status": "not_run", "reason": "dedicated test backend/account credentials required"}))
        return 2
    try:
        result = run(base, email, password, mirror_url=os.environ.get("LEARNOVA_TEST_MIRROR_URL"),
                     allow_writes=os.environ.get("LEARNOVA_TEST_ALLOW_WRITES") == "1")
    except Exception as exc:
        code = ("FIXTURE_CLEANUP_FAILED" if isinstance(exc, FixtureCleanupError)
                else exc.code if isinstance(exc, CliError) else "ACCEPTANCE_FAILED")
        print(json.dumps({"status": "failed", "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
