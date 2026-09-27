"""Generate the mirror index consumed by learnova-cli."""

from __future__ import annotations

import json
import time
from pathlib import Path

from mirror.source import ApprovedSkill


class IndexError(RuntimeError):
    pass


def load_index(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IndexError(f"cannot read existing index: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("skills"), list):
        raise IndexError("existing index has unsupported schema")
    return value


def mappings_from_index(index: dict | None) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    dirs: dict[str, str] = {}
    commits: dict[tuple[str, str], str] = {}
    if not index:
        return dirs, commits
    for skill in index["skills"]:
        cid = skill.get("community_id")
        skill_dir = skill.get("dir")
        if not isinstance(cid, str) or not isinstance(skill_dir, str):
            raise IndexError("existing index is missing community_id/dir")
        if cid in dirs and dirs[cid] != skill_dir:
            raise IndexError(f"community_id {cid} has multiple dirs")
        dirs[cid] = skill_dir
        for version in skill.get("versions", []):
            number, commit = version.get("version"), version.get("commit")
            if not isinstance(number, str) or not isinstance(commit, str):
                raise IndexError(f"{skill_dir} has invalid version mapping")
            commits[(cid, number)] = commit
    return dirs, commits


def build_index(
    skills: list[ApprovedSkill],
    dirs: dict[str, str],
    commits: dict[tuple[str, str], str],
    previous: dict | None,
) -> dict:
    items = []
    for skill in sorted(skills, key=lambda item: dirs[item.community_id]):
        versions = []
        for version in skill.versions:
            commit = commits.get((skill.community_id, version.version))
            if not commit:
                raise IndexError(f"missing content commit for {dirs[skill.community_id]}@{version.version}")
            versions.append(
                {
                    "version": version.version,
                    "changelog": version.changelog,
                    "commit": commit,
                    "size_bytes": version.size_bytes,
                    "created_at": version.created_at,
                }
            )
        latest = skill.latest
        items.append(
            {
                "dir": dirs[skill.community_id],
                "community_id": skill.community_id,
                "name": skill.name,
                "display_name": skill.display_name,
                "description": skill.description,
                "tags": list(latest.tags),
                "likes": skill.likes,
                "downloads": skill.downloads,
                "latest_version": latest.version,
                "versions": versions,
            }
        )

    result = {"schema_version": 1, "generated_at": 0.0, "skills": items}
    if previous:
        old_without_time = dict(previous)
        old_without_time["generated_at"] = 0.0
        if old_without_time == result:
            result["generated_at"] = float(previous.get("generated_at", 0.0))
            return result
    result["generated_at"] = time.time()
    return result


def write_index(path: Path, value: dict) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")
