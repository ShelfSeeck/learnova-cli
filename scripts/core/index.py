"""index.json 解析、过滤与排序。纯函数零 IO。

schema 见 docs/mirror-spec.md §3。
"""

import re

from core import versions
from core.errors import CliError
from core.skill import validate_skill_dir

SORT_KEYS = {
    "popular": lambda s: s.get("downloads", 0),
    "likes": lambda s: s.get("likes", 0),
    "newest": lambda s: s.get("versions", [{}])[-1].get("created_at", 0),
}

SUMMARY_FIELDS = ("dir", "name", "display_name", "description", "tags", "likes", "downloads", "latest_version")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


def _invalid(message: str) -> None:
    raise CliError("MIRROR_INDEX_INVALID", message)


def summarize(skill: dict) -> dict:
    """列表项摘要：去掉 versions 等大字段。"""
    return {k: skill.get(k) for k in SUMMARY_FIELDS}


def filter_skills(index: dict, keyword: str = "", tags: list[str] | None = None) -> list[dict]:
    skills = index.get("skills", [])
    if keyword:
        kw = keyword.lower()
        skills = [
            s for s in skills
            if kw in s.get("name", "").lower()
            or kw in s.get("display_name", "").lower()
            or kw in s.get("description", "").lower()
            or any(kw in str(tag).lower() for tag in s.get("tags", []))
        ]
    if tags:
        required = {t.lower() for t in tags}
        skills = [
            s for s in skills
            if required <= {t.lower() for t in s.get("tags", [])}
        ]
    return skills


def validate_index(index: dict) -> dict:
    """校验 mirror-spec v1 的安装所需最小结构。"""
    if not isinstance(index, dict) or index.get("schema_version") != 1:
        _invalid("镜像索引 schema_version 非 1")
    skills = index.get("skills")
    if not isinstance(skills, list):
        _invalid("镜像索引 skills 必须是数组")
    for skill in skills:
        if not isinstance(skill, dict):
            _invalid("镜像索引 skill 条目必须是对象")
        try:
            validate_skill_dir(skill.get("dir"))
        except CliError as exc:
            raise CliError("MIRROR_INDEX_INVALID", exc.message) from exc
        versions_list = skill.get("versions")
        if not isinstance(versions_list, list) or not versions_list:
            _invalid(f"{skill.get('dir')} 缺少 versions")
        for entry in versions_list:
            if not isinstance(entry, dict):
                _invalid(f"{skill.get('dir')} 的版本条目非法")
            if not isinstance(entry.get("version"), str) or not entry["version"]:
                _invalid(f"{skill.get('dir')} 存在非法版本号")
            if not isinstance(entry.get("commit"), str) or not COMMIT_RE.fullmatch(entry["commit"]):
                _invalid(f"{skill.get('dir')} 存在非法 commit hash")
        latest = skill.get("latest_version")
        if not isinstance(latest, str) or latest not in {v["version"] for v in versions_list}:
            _invalid(f"{skill.get('dir')} 的 latest_version 与 versions 不一致")
    return index


def sort_skills(skills: list[dict], sort: str) -> list[dict]:
    key = SORT_KEYS.get(sort, SORT_KEYS["popular"])
    return sorted(skills, key=key, reverse=True)


def find_skill(index: dict, skill_dir: str) -> dict | None:
    for s in index.get("skills", []):
        if s.get("dir") == skill_dir:
            return s
    return None


def has_newer_version(skill: dict, current: str) -> str | None:
    """索引中最新版本高于 current 时返回该版本号，否则 None。"""
    latest = skill.get("latest_version")
    if latest and versions.compare(latest, current) > 0:
        return latest
    return None


def plan_upgrades(index: dict, installed: list[dict], only: str | None = None) -> tuple[list[dict], list[dict]]:
    """对比索引与已安装清单，返回 (upgrades, skipped)。

    installed 项为 {"dir", "version"}；upgrades 项为 {"dir", "name", "from", "to"}。
    """
    by_dir = {s.get("dir"): s for s in index.get("skills", [])}
    installed_by_dir: dict[str, list[str]] = {}
    for item in installed:
        installed_by_dir.setdefault(item["dir"], []).append(item["version"])

    upgrades, skipped = [], []
    for dir_name, current_versions in installed_by_dir.items():
        if only and dir_name != only:
            continue
        skill = by_dir.get(dir_name)
        if skill is None:
            skipped.append({"dir": dir_name, "reason": "索引中不存在"})
            continue
        current = min(current_versions, key=versions.parse)
        newer = has_newer_version(skill, current)
        if newer:
            upgrades.append({"dir": dir_name, "name": skill.get("name", dir_name), "from": current, "to": newer})
        else:
            skipped.append({"dir": dir_name, "reason": "已是最新"})
    return upgrades, skipped
