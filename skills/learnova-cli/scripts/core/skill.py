"""Skill 标识与 SKILL.md 元数据的纯逻辑校验。"""

from core.errors import CliError
from core.frontmatter import parse_frontmatter


def validate_skill_dir(value: str) -> str:
    """验证镜像/安装层使用的技能目录标识是安全的单路径段。"""
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise CliError("INVALID_SKILL_DIR", f"非法技能目录标识: {value!r}", exit_code=2)
    if any(ch in value for ch in ("/", "\\", "\x00", ":")):
        raise CliError("INVALID_SKILL_DIR", f"技能目录标识不能包含路径分隔符或保留字符: {value!r}", exit_code=2)
    return value


def parse_skill_meta(text: str, expected_version: str | None = None) -> dict:
    """解析并校验安装链路所需的 SKILL.md 元数据。"""
    meta, _ = parse_frontmatter(text)
    if not meta.get("name") or not meta.get("version"):
        raise CliError("SKILL_PACKAGE_INVALID", "SKILL.md 必须包含 name 和 version")
    if expected_version is not None and meta["version"] != expected_version:
        raise CliError(
            "SKILL_PACKAGE_INVALID",
            f"技能包版本 {meta['version']} 与索引版本 {expected_version} 不一致",
        )
    return meta
