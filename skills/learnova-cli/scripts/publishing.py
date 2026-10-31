"""Safe local packaging and authenticated submission/review orchestration."""

import io
import json
import math
import os
from pathlib import Path
import re
import stat
from urllib.parse import quote, urlencode
import zipfile

import auth
from core.errors import CliError
from core.frontmatter import parse_frontmatter
from core import skill_build
from core.package_paths import is_private_path

MAX_PACKAGE_BYTES = 40 * 1024 * 1024
MAX_PACKAGE_FILES = 2000
MAX_PACKAGE_DEPTH = 32
MAX_PAGES = 100
MAX_SUBMISSIONS = 10000


def _invalid():
    raise CliError("BACKEND_RESPONSE_INVALID", "后端返回的发布或审核数据无效")


def _package_invalid():
    raise CliError("PUBLISH_PACKAGE_INVALID", "待发布目录包含无效元数据、缺失引用或不安全文件", 2)


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        _invalid()
    return value


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or any(ord(c) < 32 for c in value):
        _invalid()
    return value


def _metadata(text):
    # The server validates full YAML. Read required top-level scalars here without
    # imposing the document builder's flat frontmatter on ordinary Agent Skills.
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match:
        _package_invalid()
    lines, fields = match[1].splitlines(), {}
    for i, line in enumerate(lines):
        pair = re.match(r"^(name|description):[ \t]*(.*)$", line)
        if not pair:
            continue
        key, value = pair.groups()
        if key in fields:
            _package_invalid()
        block_marker = re.split(r"[ \t]+#", value, maxsplit=1)[0]
        if re.fullmatch(r"[>|][+-]?[1-9]?", block_marker):
            parts = []
            for following in lines[i + 1:]:
                if following and not following[0].isspace():
                    break
                parts.append(following.strip())
            fields[key] = " ".join(parts).strip()
        else:
            fields[key] = parse_frontmatter("---\n" + line + "\n---\n", strict=True)[0].get(key, "")
    if not skill_build.valid_skill_metadata(fields.get("name"), fields.get("description")):
        _package_invalid()
    return fields, text[match.end():]


def package(directory):
    """Snapshot safe regular files into memory; never rewrite the source directory."""
    root = Path(directory).absolute()
    try:
        if not root.is_dir() or root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
            _package_invalid()
        files, size = {}, 0

        def unreadable(error):
            raise error

        for current, directories, names in os.walk(root, followlinks=False, onerror=unreadable):
            for name in sorted(directories + names):
                path = Path(current) / name
                relative = path.relative_to(root).as_posix()
                entry = path.lstat()
                if (not (stat.S_ISDIR(entry.st_mode) or stat.S_ISREG(entry.st_mode))
                        or not skill_build._safe_path(relative) or len(relative.split("/")) > MAX_PACKAGE_DEPTH):
                    _package_invalid()
                if is_private_path(relative):
                    if name in directories:
                        directories.remove(name)
                    continue
                if stat.S_ISDIR(entry.st_mode):
                    continue
                if entry.st_nlink != 1 or len(files) >= MAX_PACKAGE_FILES or entry.st_size > MAX_PACKAGE_BYTES - size:
                    _package_invalid()
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as source:
                    opened = os.fstat(source.fileno())
                    if (opened.st_dev, opened.st_ino, opened.st_mode) != (entry.st_dev, entry.st_ino, entry.st_mode):
                        _package_invalid()
                    content = source.read(MAX_PACKAGE_BYTES - size + 1)
                size += len(content)
                if size > MAX_PACKAGE_BYTES:
                    _package_invalid()
                files[relative] = content
        metadata, body = _metadata(files["SKILL.md"].decode("utf-8"))
        # Validate links using the common pure validator, with only its metadata
        # view normalized. The ZIP retains the caller's original SKILL.md bytes.
        validation = {**files, "SKILL.md": "---\nname: " + json.dumps(metadata["name"])
                      + "\ndescription: " + json.dumps(metadata["description"]) + "\n---\n" + body}
        skill_build.validate_package(validation)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                archive.writestr(name, files[name])
        payload = output.getvalue()
        if len(payload) > MAX_PACKAGE_BYTES:
            _package_invalid()
        return payload, metadata
    except (OSError, ValueError, KeyError, UnicodeError, CliError) as exc:
        if isinstance(exc, CliError) and exc.code == "PUBLISH_PACKAGE_INVALID":
            raise
        _package_invalid()


def publish(ctx, directory, version, changelog):
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise CliError("BAD_USAGE", "--version 必须是 x.y.z 形式的版本号", 2)
    if not isinstance(changelog, str) or not changelog.strip() or len(changelog) > 10000 or "\x00" in changelog:
        raise CliError("BAD_USAGE", "请通过 --changelog 明确提供本次变更说明", 2)
    root = Path(directory)
    if not root.is_absolute():
        root = ctx.cwd / root
    payload, metadata = package(root)
    account = auth.current_user(ctx)
    actor = _identifier(account.get("uuid"))
    uploaded = auth.request(ctx, "POST", "/library/skills/upload", data=payload, content_type="application/zip")
    if not isinstance(uploaded, dict) or uploaded.get("user_uuid") != actor or uploaded.get("community_skill_id") is not None:
        _invalid()
    library_id = _identifier(uploaded.get("id"))
    prefix = "/library/skills/" + quote(library_id, safe="")
    form = auth.request(ctx, "GET", prefix + "/publish-form")
    if (not isinstance(form, dict) or not isinstance(form.get("library_skill"), dict)
            or form["library_skill"].get("id") != library_id
            or form["library_skill"].get("user_uuid") != actor
            or form["library_skill"].get("community_skill_id") is not None
            or form.get("community_skill") is not None or "base_submission_id" not in form
            or form["base_submission_id"] is not None):
        _invalid()
    # A ZIP upload creates a fresh library item. Matching name candidates are not
    # authorization to bind or modify an existing community skill.
    result = auth.request(ctx, "POST", prefix + "/publish", body={"version": version,
                          "changelog": changelog, "base_submission_id": form["base_submission_id"],
                          "community_skill_id": None})
    if (not isinstance(result, dict) or result.get("version") != version or result.get("submitted_by") != actor
            or result.get("status") not in {"PENDING_OWNER", "PENDING_ADMIN"}):
        _invalid()
    return {"ok": True, "submitted": True, "name": metadata["name"], "version": version,
            "status": result["status"], "library_id": library_id,
            "skill_id": _identifier(result.get("skill_id")), "version_id": _identifier(result.get("id")),
            "submission_id": _identifier(result.get("current_submission_id")),
            "message": "已提交审核，审核通过后才会上架"}


def _pages(ctx):
    items, seen, offset, total = [], set(), 0, None
    for _ in range(MAX_PAGES):
        response = auth.request(ctx, "GET", "/library/submissions?" + urlencode({"limit": 100, "offset": offset}))
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            _invalid()
        count, limit, returned_offset = response.get("total"), response.get("limit"), response.get("offset")
        if (type(count) is not int or not 0 <= count <= MAX_SUBMISSIONS or type(limit) is not int
                or not 1 <= limit <= 100 or type(returned_offset) is not int or returned_offset != offset
                or (total is not None and count != total) or len(response["items"]) > limit):
            _invalid()
        total = count
        for row in response["items"]:
            if not isinstance(row, dict):
                _invalid()
            identifier = _identifier(row.get("id"))
            if identifier in seen:
                _invalid()
            seen.add(identifier)
            items.append(row)
        offset += len(response["items"])
        if offset > total:
            _invalid()
        if offset == total:
            return items
        if not response["items"]:
            _invalid()
    _invalid()


def _safe_note(ctx, value):
    if not isinstance(value, str) or len(value) > 16384:
        _invalid()
    for key in ("access_token", "refresh_token", "password"):
        secret = ctx.auth.get(key)
        if isinstance(secret, str) and secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b", "[redacted]", value)
    value = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [redacted]", value)
    value = re.sub(r'''(?ix)["']?\b(?:access_token|refresh_token|api[_-]?key|password|authorization|cookie|secret|token)\b["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)''', "[redacted]", value)
    value = re.sub(r"(?i)https?://[^\s<>]+", "[redacted URL]", value)
    value = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[redacted email]", value)
    value = re.sub(r"(?<![\w.])(?:[A-Za-z]:[\\/]|/)[^\s,;<>]+", "[redacted path]", value)
    return "".join(c for c in value if ord(c) >= 32 or c in "\n\t")[:4096]


def _logs(ctx, row, actor):
    current = row.get("current_submission_id")
    if current is None:
        return []
    current = _identifier(current)
    path = ("/community/skills/" + quote(row["skill_id"], safe="") + "/versions/"
            + quote(row["id"], safe="") + "/submissions/" + quote(current, safe=""))
    detail = auth.request(ctx, "GET", path)
    if not isinstance(detail, dict) or not isinstance(detail.get("submission"), dict) or not isinstance(detail.get("logs"), list):
        _invalid()
    submission = detail["submission"]
    if submission.get("id") != current or submission.get("version_id") != row["id"] or submission.get("submitted_by") != actor:
        _invalid()
    if len(detail["logs"]) > 2000:
        _invalid()
    result = []
    for log in detail["logs"]:
        if (not isinstance(log, dict) or log.get("version_id") != row["id"] or log.get("submission_id") != current):
            _invalid()
        timestamp = log.get("created_at")
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            _invalid()
        result.append({"action": _text(log.get("action")), "stage": _text(log.get("stage")),
                       "status": _text(log.get("to_status")), "note": _safe_note(ctx, log.get("note", "")),
                       "created_at": timestamp})
    return result


def status(ctx, skill=None):
    if skill is not None and (not isinstance(skill, str) or not skill.strip() or len(skill) > 200 or any(ord(c) < 32 for c in skill)):
        raise CliError("BAD_USAGE", "请提供有效的技能名称或 ID", 2)
    actor = _identifier(auth.current_user(ctx).get("uuid"))
    rows = _pages(ctx)
    selected = []
    for row in rows:
        _identifier(row.get("skill_id"))
        if row.get("submitted_by") != actor:
            _invalid()
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            _invalid()
        for value in (row.get("source_library_id"), row.get("skill_name"), metadata.get("name")):
            if value is not None:
                _text(value)
        if skill is None or skill in {row["skill_id"], row.get("source_library_id"), row.get("skill_name"), metadata.get("name")}:
            selected.append(row)
    if skill is not None and len({row["skill_id"] for row in selected}) > 1:
        raise CliError("SKILL_AMBIGUOUS", "多个提交具有此名称，请使用技能 ID 查询", 2)
    result = []
    for row in selected:
        logs = _logs(ctx, row, actor)
        result.append({"skill_id": row["skill_id"], "version_id": row["id"],
                       "submission_id": row.get("current_submission_id"),
                       "name": _text(row.get("skill_name") or (row.get("metadata") or {}).get("name", row["skill_id"])),
                       "version": _text(row.get("version")), "status": _text(row.get("status")),
                       "notes": [log["note"] for log in logs if log["note"]], "logs": logs})
    return {"submissions": result, "total": len(result), "source": "backend"}
