"""Synthetic community fixtures; no account, backend or user state is consulted."""

import copy
import hashlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/learnova-cli/scripts"))

import auth
import net
from cli.context import CliContext
from core.errors import AuthError, CliError
from sources import backend, resolver
from sources.mirror import MirrorSource


def summary(identity="s1", name="renamed"):
    return {"id": identity, "name": name, "display_name": "Example", "description": "Example skill",
            "likes": 7, "downloads": 12, "version": "2.0.0", "tags": '["sample", "docs"]'}


def version(identity="v2", number="2.0.0", status="APPROVED"):
    return {"id": identity, "skill_id": "s1", "version": number, "status": status,
            "readme_md": f"README {number}", "changelog": "Example change", "tags": '["docs"]',
            "size_bytes": 400, "created_at": 2, "archive_path": "/private-placeholder"}


def mirror_index():
    return {"schema_version": 1, "generated_at": 1,
            "skills": [{"dir": "stable", "community_id": "s1", "name": "old-name",
                        "latest_version": "1.0.0", "versions": [{"version": "1.0.0", "commit": "a" * 40}]}]}


def http_error(status):
    error = CliError("HTTP_ERROR", "Synthetic HTTP failure")
    error.http_status = status
    return error


class CommunityFixture:
    def __init__(self):
        self.calls = []
        self.rows = [summary()]
        self.page_size = 1
        self.versions = [version(), version("v1", "1.0.0")]
        self.detail = {**summary(), "version": None, "latest_version": self.versions[0]}
        self.files = {"SKILL.md": b"---\nname: sample\ndescription: Example\n---\nExample skill\n",
                      "assets/picture.bin": b"\x00\xff\x01\xfe"}
        self.snapshot = "snapshot1"
        self.mutate_listing = None
        self.mutate_blob = None
        self.failure = None

    def request(self, ctx, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.failure:
            raise self.failure
        if method != "GET":
            raise AssertionError("Only read-only endpoints may be used")
        parsed = urllib.parse.urlsplit(path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/community/skills":
            offset = int(query["offset"][0])
            return {"skills": copy.deepcopy(self.rows[offset:offset + self.page_size]),
                    "total": len(self.rows), "offset": offset, "limit": self.page_size}
        if parsed.path == "/community/skills/s1":
            return copy.deepcopy(self.detail)
        if parsed.path == "/community/skills/s1/versions":
            return copy.deepcopy(self.versions)
        parts = parsed.path.split("/")
        if len(parts) < 7 or parts[:5] != ["", "community", "skills", "s1", "versions"]:
            raise AssertionError(f"Unexpected fixture route: {parsed.path}")
        if parts[5] not in {"v1", "v2"}:
            raise AssertionError("Version route must use the version ID")
        pinned = len(parts) >= 9 and parts[6:8] == ["submissions", self.snapshot]
        suffix = parts[8:] if pinned else parts[6:]
        relative = query["path"][0]
        if suffix == ["files"]:
            if relative != "." and self.snapshot and not pinned:
                raise AssertionError("Nested lists must use the pinned snapshot")
            entries, directories = [], set()
            for name, content in self.files.items():
                item = Path(name)
                if item.parent.as_posix() == relative:
                    entries.append({"name": item.name, "path": name, "rel_path": f"skill/{name}",
                                    "is_dir": False, "size": len(content), "size_bytes": len(content),
                                    "sha256": hashlib.sha256(content).hexdigest(), "is_text": False})
                elif relative == "." and len(item.parts) > 1:
                    directories.add(item.parts[0])
            entries.extend({"name": name, "path": name, "is_dir": True} for name in sorted(directories))
            result = {"path": relative, "entries": entries, "submission_id": self.snapshot}
            if self.mutate_listing:
                self.mutate_listing(result)
            return result
        if suffix == ["files", "blob"]:
            if self.snapshot and not pinned:
                raise AssertionError("Blobs must use the pinned snapshot")
            if not kwargs.get("binary") or kwargs.get("max_bytes") != len(self.files[relative]) + 1:
                raise AssertionError("Raw downloads must have a per-file byte bound")
            content = self.files[relative]
            return self.mutate_blob(content) if self.mutate_blob else content
        raise AssertionError(f"Unexpected fixture route: {parsed.path}")


class BackendSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = Path(self.tmp.name) / "candidate"
        self.ctx = CliContext(config={"backend_url": "https://example.invalid"})
        self.source = backend.BackendSource(self.ctx)
        self.fixture = CommunityFixture()
        self.index = mirror_index()
        self.mapping = patch.object(self.source.mirror, "_index", side_effect=lambda: (copy.deepcopy(self.index), False))
        self.mapping.start()
        self.addCleanup(self.mapping.stop)
        self.requests = patch.object(auth, "request", side_effect=self.fixture.request)
        self.requests.start()
        self.addCleanup(self.requests.stop)

    def assert_code(self, code, callback):
        with self.assertRaises(CliError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_paginated_search_preserves_mapping_and_counts_unmapped(self):
        self.fixture.rows = [summary(), summary("s2", "unmapped"), summary("s3", "renamed")]
        self.index["skills"].append({**self.index["skills"][0], "dir": "second--s3", "community_id": "s3"})
        result = self.source.search("a b", ["docs", "sample"], "likes")
        self.assertEqual([row["dir"] for row in result["skills"]], ["stable", "second--s3"])
        self.assertEqual((result["total"], result["backend_total"], result["unmapped_total"]), (2, 3, 1))
        self.assertEqual(result["skills"][0]["tags"], ["sample", "docs"])
        self.assertEqual((result["source"], result["generated_at"], result["stale"]), ("backend", None, False))
        queries = [urllib.parse.parse_qs(urllib.parse.urlsplit(path).query) for _, path, _ in self.fixture.calls]
        self.assertEqual([q["offset"] for q in queries], [["0"], ["1"], ["2"]])
        self.assertEqual(queries[0]["sort"], ["most_liked"])
        self.assertEqual(queries[0]["tag"], ["docs", "sample"])
        self.assertEqual(queries[0]["keyword"], ["a b"])

    def test_empty_search_is_complete(self):
        self.fixture.rows = []
        self.assertEqual(self.source.search("missing", [], "popular")["total"], 0)

    def test_pagination_rejects_repeated_rows(self):
        self.fixture.rows = [summary(), summary()]
        self.assert_code("BACKEND_RESPONSE_INVALID", lambda: self.source.search("", [], "popular"))

    def test_pagination_rejects_empty_nonterminal_page_and_invalid_total(self):
        for page in ({"skills": [], "total": 1, "limit": 1, "offset": 0},
                     {"skills": [], "total": True, "limit": 1, "offset": 0},
                     {"skills": [], "total": backend.MAX_RESULTS + 1, "limit": 1, "offset": 0}):
            with self.subTest(page=page), patch.object(auth, "request", return_value=page):
                self.assert_code("BACKEND_RESPONSE_INVALID", lambda: self.source.search("", [], "popular"))

    def test_pagination_has_finite_page_limit(self):
        self.fixture.rows = [summary("s1"), summary("s2")]
        with patch.object(backend, "MAX_PAGES", 1):
            self.assert_code("BACKEND_RESPONSE_INVALID", lambda: self.source.search("", [], "popular"))

    def test_info_approved_versions_and_readme_are_whitelisted(self):
        self.fixture.versions.insert(0, version("pending", "3.0.0", "PENDING"))
        result = self.source.info("stable")
        self.assertEqual(result["latest_version"], "2.0.0")
        self.assertEqual(result["readme"], "README 2.0.0")
        self.assertEqual([row["version"] for row in result["versions"]], ["2.0.0", "1.0.0"])
        self.assertEqual(result["tags"], ["docs"])
        self.assertNotIn("archive_path", json.dumps(result))
        self.assertNotIn("owner_uuid", result)

    def test_pending_detail_latest_does_not_become_installable(self):
        self.fixture.detail["latest_version"] = version("pending", "3.0.0", "PENDING")
        self.assertEqual(self.source.info("stable")["latest_version"], "2.0.0")
        self.assert_code("VERSION_NOT_FOUND", lambda: self.source.fetch("stable", "3.0.0", self.dest))

    def test_null_readme_is_empty_and_ui_size_is_not_integrity_metadata(self):
        self.fixture.versions[0]["readme_md"] = None
        self.assertEqual(self.source.info("stable")["readme"], "")
        def mutation(listing):
            for entry in listing["entries"]:
                entry["size"] = "display size"
        self.fixture.mutate_listing = mutation
        self.assertTrue(self.source.fetch("stable", None, self.dest).is_dir())

    def test_no_approved_versions_is_not_a_public_skill(self):
        self.fixture.versions = [version("pending", "3.0.0", "PENDING")]
        self.assert_code("SKILL_NOT_FOUND", lambda: self.source.info("stable"))

    def test_invalid_tags_and_foreign_version_are_rejected(self):
        for tags in ([], "{}", "[1]", "invalid"):
            self.fixture.rows[0]["tags"] = tags
            self.assert_code("BACKEND_RESPONSE_INVALID", lambda: self.source.search("", [], "popular"))
        self.fixture.versions[0]["skill_id"] = "different"
        self.assert_code("BACKEND_RESPONSE_INVALID", lambda: self.source.info("stable"))

    def test_unmapped_name_is_never_guessed(self):
        self.assert_code("SKILL_NOT_FOUND", lambda: self.source.info("renamed"))
        self.fixture.rows.append(summary("s2"))
        error = self.assert_code("SKILL_AMBIGUOUS", lambda: self.source.info("renamed"))
        self.assertEqual(error.exit_code, 2)

    def test_ambiguous_mapping_is_rejected(self):
        self.index["skills"].append({**self.index["skills"][0], "dir": "another"})
        self.assert_code("SKILL_AMBIGUOUS", lambda: self.source.info("stable"))

    def test_fetch_old_version_preserves_binary_and_adds_sidecar(self):
        result = self.source.fetch("stable", "1.0.0", self.dest)
        self.assertEqual(result, self.dest / "stable")
        self.assertEqual((result / "assets/picture.bin").read_bytes(), self.fixture.files["assets/picture.bin"])
        self.assertEqual((result / "SKILL.md").read_bytes(), self.fixture.files["SKILL.md"])
        self.assertEqual(json.loads((result / ".learnova-install.json").read_text()), {"dir": "stable", "version": "1.0.0"})
        file_calls = [path for _, path, _ in self.fixture.calls if "/files" in path]
        self.assertTrue(file_calls)
        self.assertTrue(all("/versions/v1/" in path for path in file_calls))
        self.assertEqual(list(self.dest.iterdir()), [result])

    def test_fetch_legacy_snapshot_and_default_version(self):
        self.fixture.snapshot = None
        result = self.source.fetch("stable", None, self.dest)
        self.assertEqual(json.loads((result / ".learnova-install.json").read_text())["version"], "2.0.0")

    def test_invalid_skill_identifier_is_rejected_before_network(self):
        self.assert_code("INVALID_SKILL_DIR", lambda: self.source.fetch("../outside", None, self.dest))
        self.assertEqual(self.fixture.calls, [])

    def test_paths_links_duplicate_paths_and_parent_mismatch_are_rejected(self):
        for unsafe in ("../outside", "/outside", "a\\b", "C:/file", "a//b", "a/./b", "a\x00b", "NUL.txt", "trail."):
            def mutation(listing, path=unsafe):
                listing["entries"][0]["path"] = path
            self.fixture.mutate_listing = mutation
            with self.subTest(path=unsafe):
                self.assert_code("SKILL_PACKAGE_INVALID", lambda: self.source.fetch("stable", None, self.dest))
        for mutation in (lambda listing: listing["entries"][0].update(type="symlink"),
                         lambda listing: listing["entries"][0].update(is_symlink=True),
                         lambda listing: listing["entries"].append(copy.deepcopy(listing["entries"][0])),
                         lambda listing: listing["entries"][0].update(path="other/SKILL.md")):
            self.fixture.mutate_listing = mutation
            self.assert_code("SKILL_PACKAGE_INVALID", lambda: self.source.fetch("stable", None, self.dest))
        self.assertFalse(self.dest.exists())

    def test_depth_count_member_and_total_limits(self):
        for key, value in (("MAX_PACKAGE_DEPTH", 1), ("MAX_PACKAGE_ENTRIES", 1),
                           ("MAX_MEMBER_BYTES", 1), ("MAX_PACKAGE_BYTES", 1)):
            with self.subTest(limit=key), patch.object(backend, key, value):
                self.assert_code("SKILL_PACKAGE_INVALID", lambda: self.source.fetch("stable", None, self.dest))

    def test_sha_length_and_frontmatter_mismatch_clean_partial_package(self):
        for mutate in (lambda value: b"x" * len(value), lambda value: value + b"x"):
            self.fixture.mutate_blob = mutate
            self.assert_code("SKILL_PACKAGE_INVALID", lambda: self.source.fetch("stable", None, self.dest))
            self.assertEqual(list(self.dest.iterdir()), [])
        self.fixture.mutate_blob = None
        self.fixture.files["SKILL.md"] = b"---\nname: example\nversion: 9.0.0\n---\n"
        self.assert_code("SKILL_PACKAGE_INVALID", lambda: self.source.fetch("stable", None, self.dest))
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_missing_skill_md_and_changed_snapshot_are_rejected(self):
        del self.fixture.files["SKILL.md"]
        self.assert_code("SKILL_PACKAGE_INVALID", lambda: self.source.fetch("stable", None, self.dest))
        self.fixture.mutate_listing = lambda listing: listing.update(submission_id="changed") if listing["path"] != "." else None
        self.assert_code("SKILL_PACKAGE_INVALID", lambda: self.source.fetch("stable", None, self.dest))

    def test_explicit_not_found_is_translated_and_json_error_is_invalid(self):
        self.fixture.failure = http_error(404)
        self.assert_code("SKILL_NOT_FOUND", lambda: self.source.info("stable"))
        decode = json.JSONDecodeError("synthetic", "", 0)
        error = CliError("HTTP_ERROR", "invalid JSON")
        error.__cause__ = decode
        self.fixture.failure = error
        self.assert_code("BACKEND_RESPONSE_INVALID", lambda: self.source.search("", [], "popular"))


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.ctx = CliContext(config={"backend_url": "https://example.invalid"},
                              auth={"backend_origin": "https://example.invalid:443", "refresh_token": "fixture-refresh"})

    def chain(self, failure=None):
        source = resolver.FallbackSource(self.ctx)
        source.backend = Mock(name="backend")
        source.backend.name = "backend"
        source.mirror = Mock(name="mirror")
        source.mirror.name = "mirror"
        source.active = source.backend
        source.backend.search.return_value = {"source": "backend"}
        source.mirror.search.return_value = {"source": "mirror"}
        source.backend.search.side_effect = failure
        return source

    def test_offline_missing_backend_and_unusable_auth_use_mirror_without_network(self):
        cases = [CliContext(config=self.ctx.config, auth=self.ctx.auth, offline=True),
                 CliContext(config={}, auth=self.ctx.auth), CliContext(config=self.ctx.config),
                 CliContext(config=self.ctx.config, auth={**self.ctx.auth, "backend_origin": "https://other.invalid:443"})]
        for ctx in cases:
            with self.subTest(ctx=ctx), patch.object(auth, "request") as request, patch.object(net, "request") as network:
                self.assertIsInstance(resolver.resolve(ctx), MirrorSource)
                request.assert_not_called()
                network.assert_not_called()

    def test_strict_offline_search_reads_cache_only(self):
        self.ctx.offline = True
        self.ctx.config["mirror_repo_url"] = "https://example.invalid/mirror"
        with patch("store.load_index_cache", return_value=(mirror_index(), True)), patch.object(net, "request") as request:
            result = resolver.resolve(self.ctx).search("", [], "popular")
        self.assertEqual(result["source"], "mirror")
        self.assertTrue(result["stale"])
        request.assert_not_called()

    def test_authenticated_source_prefers_backend(self):
        self.assertIsInstance(resolver.resolve(self.ctx), resolver.FallbackSource)
        source = self.chain()
        self.assertEqual(source.search("", [], "popular")["source"], "backend")
        self.assertEqual(source.name, "backend")
        source.mirror.search.assert_not_called()

    def test_recoverable_faults_fallback_and_record_actual_source(self):
        failures = [CliError("UNREACHABLE", "fixture"), CliError("HTTP_TIMEOUT", "fixture"),
                    AuthError("UNAUTHORIZED", "fixture"), CliError("HTTP_ERROR", "interrupted"),
                    http_error(401), http_error(403), http_error(500), http_error(503)]
        for failure in failures:
            with self.subTest(code=failure.code, status=getattr(failure, "http_status", None)):
                source = self.chain(failure)
                self.assertEqual(source.search("", [], "popular")["source"], "mirror")
                self.assertEqual(source.name, "mirror")
                source.search("", [], "popular")
                self.assertEqual(source.backend.search.call_count, 1)

    def test_invalid_package_response_and_not_found_never_fallback(self):
        failures = [CliError(code, "fixture") for code in
                    ("SKILL_PACKAGE_INVALID", "BACKEND_RESPONSE_INVALID", "SKILL_NOT_FOUND", "VERSION_NOT_FOUND", "SKILL_AMBIGUOUS", "HTTP_RESPONSE_TOO_LARGE")]
        failures.extend(http_error(status) for status in (400, 404, 409, 422, 429))
        for failure in failures:
            source = self.chain(failure)
            with self.subTest(code=failure.code), self.assertRaises(CliError) as raised:
                source.search("", [], "popular")
            self.assertIs(raised.exception, failure)
            source.mirror.search.assert_not_called()

    def test_fetch_partial_backend_is_removed_before_mirror_attempt(self):
        source = self.chain()
        attempts = []
        def broken(skill_dir, version, dest):
            candidate = dest / skill_dir
            candidate.mkdir()
            (candidate / "contaminated").write_bytes(b"partial")
            attempts.append(dest)
            raise CliError("HTTP_TIMEOUT", "fixture")
        def good(skill_dir, version, dest):
            self.assertFalse(attempts[0].exists())
            self.assertEqual(list(dest.iterdir()), [])
            candidate = dest / skill_dir
            candidate.mkdir()
            (candidate / "clean").write_bytes(b"mirror")
            return candidate
        source.backend.fetch.side_effect = broken
        source.mirror.fetch.side_effect = good
        with tempfile.TemporaryDirectory() as tmp:
            result = source.fetch("stable", "1.0.0", Path(tmp))
            self.assertEqual([path.name for path in result.iterdir()], ["clean"])
            self.assertEqual([path.name for path in Path(tmp).iterdir()], ["stable"])
            self.assertEqual(source.name, "mirror")

    def test_fetch_invalid_package_cleans_without_fallback(self):
        source = self.chain()
        def broken(skill_dir, version, dest):
            (dest / skill_dir).mkdir()
            raise CliError("SKILL_PACKAGE_INVALID", "fixture")
        source.backend.fetch.side_effect = broken
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CliError):
                source.fetch("stable", None, Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])
        source.mirror.fetch.assert_not_called()


class LocalTransportTests(unittest.TestCase):
    def test_authenticated_binary_transport_and_invalid_json_no_fallback(self):
        fixture, received = CommunityFixture(), []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                received.append((self.path, self.headers.get("Authorization"), self.headers.get("Accept")))
                if "keyword=invalid" in self.path:
                    payload, content_type = b"{invalid", "application/json"
                else:
                    raw = "/files/blob?" in self.path
                    args = {"binary": True, "max_bytes": len(fixture.files[urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["path"][0]]) + 1} if raw else {}
                    response = fixture.request(None, "GET", self.path, **args)
                    payload = response if raw else json.dumps(response).encode()
                    content_type = "application/octet-stream" if raw else "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            ctx = CliContext(config={"backend_url": base}, auth={"backend_origin": base,
                             "refresh_token": "fixture-refresh", "access_token": "fixture-access"})
            source = resolver.FallbackSource(ctx)
            with patch.object(source.backend.mirror, "_index", return_value=(mirror_index(), False)), \
                 patch.object(source.mirror, "search") as fallback, tempfile.TemporaryDirectory() as tmp:
                output = source.fetch("stable", None, Path(tmp))
                self.assertEqual((output / "assets/picture.bin").read_bytes(), fixture.files["assets/picture.bin"])
                with self.assertRaises(CliError) as raised:
                    source.search("invalid", [], "popular")
                self.assertEqual(raised.exception.code, "BACKEND_RESPONSE_INVALID")
                fallback.assert_not_called()
            self.assertTrue(all(header == "Bearer fixture-access" for _, header, _ in received))
            self.assertTrue(all(accept == ("application/octet-stream" if "/files/blob?" in path else "application/json")
                                for path, _, accept in received))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
