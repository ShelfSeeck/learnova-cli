"""实时社区只读源；目录身份始终来自镜像索引。"""

import hashlib
import json
import re
import tempfile
import urllib.parse
from pathlib import Path, PurePosixPath

import auth
from core.errors import CliError
from core.skill import parse_skill_meta, validate_skill_dir, write_install_metadata
from sources.mirror import MirrorSource

PAGE_SIZE = 100
MAX_PAGES = 100
MAX_RESULTS = 10000
MAX_VERSIONS = 1000
MAX_PACKAGE_ENTRIES = 2000
MAX_PACKAGE_DEPTH = 32
MAX_MEMBER_BYTES = 25 * 1024 * 1024
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
SORTS = {"popular": "popular", "newest": "newest", "likes": "most_liked"}
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _invalid(message="后端返回结构不符合社区读取契约"):
    raise CliError("BACKEND_RESPONSE_INVALID", message)


def _package_invalid():
    raise CliError("SKILL_PACKAGE_INVALID", "社区技能包的路径、类型或完整性校验失败")


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        _invalid()
    return value


def _integer(value):
    return type(value) is int and value >= 0


def _tags(value):
    if value is None:
        return []
    if not isinstance(value, str):
        _invalid("后端 tags 必须是 JSON 数组字符串")
    try:
        decoded = json.loads(value)
    except (ValueError, TypeError):
        _invalid("后端 tags 不是有效 JSON 数组")
    if not isinstance(decoded, list) or any(not isinstance(tag, str) for tag in decoded):
        _invalid("后端 tags 必须包含字符串")
    return decoded


def _safe_path(value):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(ch) < 32 or 0xD800 <= ord(ch) <= 0xDFFF
                   or ch in '\\:\x7f?*|<>"' for ch in value)):
        _package_invalid()
    parts = value.split("/")
    if len(parts) > MAX_PACKAGE_DEPTH or any(part in {"", ".", ".."} for part in parts):
        _package_invalid()
    # Windows 的保留名称及尾随点/空格不能作为跨平台安装文件名。
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if any(part.endswith((".", " ")) or part.split(".")[0].upper() in reserved for part in parts):
        _package_invalid()
    return PurePosixPath(value)


class BackendSource:
    name = "backend"

    def __init__(self, ctx):
        self.ctx = ctx
        self.mirror = MirrorSource(ctx)

    def _get(self, path, *, missing=None, **kwargs):
        try:
            return auth.request(self.ctx, "GET", path, **kwargs)
        except CliError as exc:
            if missing and getattr(exc, "http_status", None) == 404:
                raise CliError(missing, "社区技能或版本不存在") from None
            # net 的 JSON 解码错误与连接中断共用 HTTP_ERROR，前者不可降级掩盖。
            if isinstance(exc.__cause__, (json.JSONDecodeError, UnicodeDecodeError)):
                _invalid("后端没有返回有效 JSON")
            raise

    def _pages(self, keyword, tags, sort):
        results, seen, offset = [], set(), 0
        for _ in range(MAX_PAGES):
            query = urllib.parse.urlencode(
                [("keyword", keyword), *(('tag', tag) for tag in tags),
                 ("sort", SORTS.get(sort, "popular")), ("limit", PAGE_SIZE), ("offset", offset)]
            )
            page = self._get(f"/community/skills?{query}")
            if (not isinstance(page, dict) or not isinstance(page.get("skills"), list)
                    or not _integer(page.get("total")) or page["total"] > MAX_RESULTS
                    or type(page.get("offset")) is not int or page["offset"] != offset
                    or not _integer(page.get("limit")) or page["limit"] == 0
                    or len(page["skills"]) > page["limit"]):
                _invalid("后端分页结构或结果数量非法")
            rows = page["skills"]
            if offset + len(rows) > page["total"] or (not rows and offset < page["total"]):
                _invalid("后端分页没有取得进展")
            for row in rows:
                if not isinstance(row, dict):
                    _invalid()
                identity = _identifier(row.get("id"))
                if identity in seen:
                    _invalid("后端分页包含重复技能")
                seen.add(identity)
                results.append(row)
            offset += len(rows)
            if offset >= page["total"]:
                return results
        _invalid("后端分页超过安全上限")

    def _mapping(self):
        index, _ = self.mirror._index()
        by_dir, by_id = {}, {}
        for skill in index["skills"]:
            identity = skill.get("community_id")
            if identity is None:
                continue
            _identifier(identity)
            if skill["dir"] in by_dir or identity in by_id:
                raise CliError("SKILL_AMBIGUOUS", "镜像目录与社区标识的映射不唯一", exit_code=2)
            by_dir[skill["dir"]] = skill
            by_id[identity] = skill["dir"]
        return by_dir, by_id

    def _identity(self, skill_dir):
        validate_skill_dir(skill_dir)
        by_dir, _ = self._mapping()
        if skill_dir in by_dir:
            return by_dir[skill_dir]["community_id"]
        candidates = self._pages(skill_dir, [], "popular")
        exact = [row for row in candidates if skill_dir in (row.get("name"), row.get("display_name"))]
        if len(exact) > 1:
            raise CliError("SKILL_AMBIGUOUS", "名称对应多个社区技能，请使用 search 返回的 dir", exit_code=2)
        raise CliError("SKILL_NOT_FOUND", "技能目录不存在或尚未进入镜像，请使用 search 返回的 dir")

    @staticmethod
    def _meta():
        return {"source": "backend", "generated_at": None, "stale": False}

    @staticmethod
    def _summary(row, skill_dir, version=None):
        if not isinstance(row.get("name"), str) or not row["name"]:
            _invalid()
        for key in ("display_name", "description"):
            if row.get(key) is not None and not isinstance(row[key], str):
                _invalid()
        for key in ("likes", "downloads"):
            if not _integer(row.get(key)):
                _invalid()
        latest = version["version"] if version is not None else row.get("version")
        if not isinstance(latest, str) or not latest:
            _invalid("后端没有返回已发布版本号")
        return {
            "dir": skill_dir, "name": row["name"], "display_name": row.get("display_name"),
            "description": row.get("description"), "tags": _tags(version.get("tags") if version else row.get("tags")),
            "likes": row["likes"], "downloads": row["downloads"], "latest_version": latest,
        }

    def search(self, keyword: str, tags: list[str], sort: str) -> dict:
        rows = self._pages(keyword, tags, sort)
        _, by_id = self._mapping()
        skills = [self._summary(row, by_id[row["id"]]) for row in rows if row["id"] in by_id]
        return {"skills": skills, "total": len(skills), "backend_total": len(rows),
                "unmapped_total": len(rows) - len(skills), **self._meta()}

    def _details(self, skill_dir):
        identity = self._identity(skill_dir)
        root = f"/community/skills/{identity}"
        detail = self._get(root, missing="SKILL_NOT_FOUND")
        if not isinstance(detail, dict) or detail.get("id") != identity:
            _invalid()
        rows = self._get(f"{root}/versions", missing="SKILL_NOT_FOUND")
        if not isinstance(rows, list) or len(rows) > MAX_VERSIONS:
            _invalid()
        approved, ids, names = [], set(), set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("status"), str):
                _invalid()
            if row["status"] != "APPROVED":
                continue
            version_id = _identifier(row.get("id"))
            version = row.get("version")
            if (row.get("skill_id") != identity or not isinstance(version, str) or not version
                    or len(version) > 200 or version_id in ids or version in names
                    or (row.get("readme_md") is not None and not isinstance(row["readme_md"], str))
                    or not _integer(row.get("size_bytes"))):
                _invalid("后端已发布版本结构非法")
            _tags(row.get("tags"))
            ids.add(version_id)
            names.add(version)
            approved.append({**row, "readme_md": row.get("readme_md") or ""})
        if not approved:
            raise CliError("SKILL_NOT_FOUND", "社区技能没有可见的已发布版本")
        latest = detail.get("latest_version")
        if latest is not None and not isinstance(latest, dict):
            _invalid()
        preferred = latest.get("id") if latest and latest.get("status") == "APPROVED" else None
        selected = next((row for row in approved if row["id"] == preferred), None)
        if selected is None:
            selected = next((row for row in approved if row["version"] == detail.get("version")), approved[0])
        return root, detail, approved, selected

    def info(self, skill_dir: str) -> dict:
        _, detail, approved, selected = self._details(skill_dir)
        fields = ("version", "changelog", "size_bytes", "created_at")
        return {**self._summary(detail, skill_dir, selected),
                "versions": [{key: row.get(key) for key in fields} for row in approved],
                "readme": selected["readme_md"], **self._meta()}

    def _files(self, root):
        pending, seen, files, total = ["."], set(), [], 0
        snapshot = None
        file_root = root
        while pending:
            directory = pending.pop()
            listing = self._get(f"{file_root}/files?{urllib.parse.urlencode({'path': directory})}")
            if (not isinstance(listing, dict) or listing.get("path") != directory
                    or not isinstance(listing.get("entries"), list)):
                _package_invalid()
            submission = listing.get("submission_id")
            if directory == ".":
                snapshot = _identifier(submission) if submission is not None else None
                if snapshot:
                    file_root = f"{root}/submissions/{snapshot}"
            elif submission != snapshot:
                _package_invalid()
            for entry in listing["entries"]:
                if not isinstance(entry, dict) or type(entry.get("is_dir")) is not bool:
                    _package_invalid()
                path = _safe_path(entry.get("path"))
                if (path.parts[0].casefold() == ".learnova-install.json"
                        and (entry["is_dir"] or len(path.parts) != 1)):
                    _package_invalid()
                if (path.name != entry.get("name") or str(path.parent) != directory
                        or str(path).casefold() in seen or len(seen) >= MAX_PACKAGE_ENTRIES
                        or entry.get("is_symlink") or entry.get("is_link")
                        or entry.get("type", "dir" if entry["is_dir"] else "file")
                        not in ({"dir", "directory"} if entry["is_dir"] else {"file"})):
                    _package_invalid()
                seen.add(str(path).casefold())
                if entry["is_dir"]:
                    pending.append(str(path))
                    continue
                size = entry.get("size_bytes")
                digest = entry.get("sha256")
                if (not _integer(size) or size > MAX_MEMBER_BYTES
                        or not isinstance(digest, str) or not SHA256.fullmatch(digest)):
                    _package_invalid()
                total += size
                if total > MAX_PACKAGE_BYTES:
                    _package_invalid()
                files.append((str(path), size, digest.lower()))
        return file_root, files

    def fetch(self, skill_dir: str, version: str | None, dest: Path) -> Path:
        root, _, approved, selected = self._details(skill_dir)
        if version is not None:
            selected = next((row for row in approved if row["version"] == version), None)
            if selected is None:
                raise CliError("VERSION_NOT_FOUND", "社区技能不存在指定的已发布版本")
        file_root, files = self._files(f"{root}/versions/{selected['id']}")
        target = Path(dest) / skill_dir
        if target.exists() or target.is_symlink():
            _package_invalid()
        Path(dest).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="learnova-backend-", dir=dest) as tmp:
            candidate = Path(tmp) / skill_dir
            candidate.mkdir()
            for path, size, digest in files:
                try:
                    content = self._get(
                        f"{file_root}/files/blob?{urllib.parse.urlencode({'path': path})}",
                        binary=True, max_bytes=size + 1,
                    )
                except CliError as exc:
                    if exc.code == "HTTP_RESPONSE_TOO_LARGE" or getattr(exc, "http_status", None) == 404:
                        _package_invalid()
                    raise
                if not isinstance(content, bytes) or len(content) != size or hashlib.sha256(content).hexdigest() != digest:
                    _package_invalid()
                output = candidate.joinpath(*PurePosixPath(path).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(content)
            skill_md = candidate / "SKILL.md"
            if not skill_md.is_file():
                _package_invalid()
            try:
                parse_skill_meta(skill_md.read_text(encoding="utf-8"), selected["version"])
            except (OSError, UnicodeDecodeError):
                _package_invalid()
            write_install_metadata(candidate, skill_dir, selected["version"])
            candidate.replace(target)
        return target
