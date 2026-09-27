"""Read APPROVED community skills from a Learnova SQLite database."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovedVersion:
    version: str
    changelog: str
    tags: tuple[str, ...]
    archive_path: str
    archive_dir: Path
    size_bytes: int
    created_at: float


@dataclass(frozen=True)
class ApprovedSkill:
    community_id: str
    name: str
    display_name: str
    description: str
    likes: int
    downloads: int
    versions: tuple[ApprovedVersion, ...]

    @property
    def latest(self) -> ApprovedVersion:
        return self.versions[-1]


def _connect_read_only(database: Path) -> sqlite3.Connection:
    database = database.resolve()
    if not database.is_file():
        raise SourceError(f"database not found: {database}")
    return sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)


def _parse_tags(raw: str, *, skill: str, version: str) -> tuple[str, ...]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise SourceError(f"{skill}@{version} has invalid tags JSON") from exc
    if not isinstance(value, list) or not all(isinstance(tag, str) for tag in value):
        raise SourceError(f"{skill}@{version} tags must be a string array")
    return tuple(value)


def _resolve_archive(archive_root: Path, stored_path: str) -> Path:
    raw = PurePosixPath(stored_path)
    if raw.is_absolute() or ".." in raw.parts:
        raise SourceError(f"unsafe archive_path: {stored_path}")
    parts = list(raw.parts)
    if parts and parts[0] == archive_root.name:
        parts = parts[1:]
    candidate = archive_root.joinpath(*parts).resolve()
    root = archive_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SourceError(f"archive_path escapes archive root: {stored_path}") from exc
    skill_dir = candidate / "skill" if (candidate / "skill").is_dir() else candidate
    if not (skill_dir / "SKILL.md").is_file():
        raise SourceError(f"archive is missing SKILL.md: {stored_path}")
    return skill_dir


def validate_skill_tree(path: Path) -> None:
    """Reject entries that cannot be safely represented by the mirror/consumer."""
    if path.is_symlink():
        raise SourceError(f"skill archive root may not be a symlink: {path}")
    for entry in path.rglob("*"):
        rel = entry.relative_to(path)
        if ".git" in rel.parts:
            raise SourceError(f"skill archive contains .git: {path}")
        if entry.is_symlink():
            raise SourceError(f"skill archive contains symlink: {entry}")


def load_approved(database: Path, archive_root: Path) -> list[ApprovedSkill]:
    archive_root = archive_root.resolve()
    if not archive_root.is_dir():
        raise SourceError(f"archive root not found: {archive_root}")

    con = _connect_read_only(database)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT
                s.id AS community_id,
                s.name,
                COALESCE(s.display_name, '') AS display_name,
                s.description,
                s.likes,
                s.downloads,
                v.version,
                v.changelog,
                v.tags,
                v.archive_path,
                v.size_bytes,
                v.created_at
            FROM community_skills s
            JOIN community_skill_versions v ON v.skill_id = s.id
            WHERE v.status = ?
            ORDER BY v.created_at ASC, s.id ASC, v.version ASC
            """,
            ("APPROVED",),
        ).fetchall()
    except sqlite3.Error as exc:
        raise SourceError(f"failed to read Learnova community tables: {exc}") from exc
    finally:
        con.close()

    grouped: dict[str, dict] = {}
    for row in rows:
        cid = row["community_id"]
        item = grouped.setdefault(
            cid,
            {
                "community_id": cid,
                "name": row["name"],
                "display_name": row["display_name"],
                "description": row["description"],
                "likes": int(row["likes"]),
                "downloads": int(row["downloads"]),
                "versions": [],
            },
        )
        item["versions"].append(
            ApprovedVersion(
                version=row["version"],
                changelog=row["changelog"] or "",
                tags=_parse_tags(row["tags"], skill=row["name"], version=row["version"]),
                archive_path=row["archive_path"],
                archive_dir=_resolve_archive(archive_root, row["archive_path"]),
                size_bytes=int(row["size_bytes"]),
                created_at=float(row["created_at"]),
            )
        )

    return [
        ApprovedSkill(
            community_id=item["community_id"],
            name=item["name"],
            display_name=item["display_name"],
            description=item["description"],
            likes=item["likes"],
            downloads=item["downloads"],
            versions=tuple(item["versions"]),
        )
        for item in grouped.values()
    ]
