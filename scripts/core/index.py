"""index.json 解析、过滤与排序。纯函数零 IO。

schema 见 docs/mirror-spec.md §3。
"""

from core import versions

SORT_KEYS = {
    "popular": lambda s: s.get("downloads", 0),
    "likes": lambda s: s.get("likes", 0),
    "newest": lambda s: s.get("versions", [{}])[-1].get("created_at", 0),
}


def filter_skills(index: dict, keyword: str = "", tags: list[str] | None = None) -> list[dict]:
    skills = index.get("skills", [])
    if keyword:
        kw = keyword.lower()
        skills = [
            s for s in skills
            if kw in s.get("name", "").lower()
            or kw in s.get("display_name", "").lower()
            or kw in s.get("description", "").lower()
        ]
    if tags:
        required = {t.lower() for t in tags}
        skills = [
            s for s in skills
            if required <= {t.lower() for t in s.get("tags", [])}
        ]
    return skills


def sort_skills(skills: list[dict], sort: str) -> list[dict]:
    key = SORT_KEYS.get(sort, SORT_KEYS["popular"])
    return sorted(skills, key=key, reverse=True)


def find_skill(index: dict, skill_dir: str) -> dict | None:
    for s in index.get("skills", []):
        if s.get("dir") == skill_dir:
            return s
    return None


def latest_version_of(skill: dict) -> str | None:
    return skill.get("latest_version")


def has_newer_version(skill: dict, current: str) -> str | None:
    """索引中最新版本高于 current 时返回该版本号，否则 None。"""
    latest = skill.get("latest_version")
    if latest and versions.compare(latest, current) > 0:
        return latest
    return None
