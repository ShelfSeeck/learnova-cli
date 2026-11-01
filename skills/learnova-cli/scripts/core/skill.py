"""Skill 标识、SKILL.md 与 Learnova 安装元数据。"""

import json
from pathlib import Path

from core.errors import CliError
from core.frontmatter import parse_frontmatter

INSTALL_METADATA_FILE = ".learnova-install.json"


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
    if not meta.get("name"):
        raise CliError("SKILL_PACKAGE_INVALID", "SKILL.md 必须包含 name")
    declared_version = meta.get("version")
    if expected_version is not None and declared_version and declared_version != expected_version:
        raise CliError(
            "SKILL_PACKAGE_INVALID",
            f"技能包版本 {declared_version} 与索引版本 {expected_version} 不一致",
        )
    return meta


def write_install_metadata(skill_root: Path, skill_dir: str, version: str) -> None:
    """写入 Learnova 自己维护的安装版本 sidecar，不修改原 SKILL.md。"""
    validate_skill_dir(skill_dir)
    if not isinstance(version, str) or not version:
        raise CliError("SKILL_PACKAGE_INVALID", "索引版本不能为空")
    path = skill_root / INSTALL_METADATA_FILE
    path.write_text(
        json.dumps({"dir": skill_dir, "version": version}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_install_metadata(skill_root: Path) -> dict:
    """读取安装 sidecar；缺失或损坏时返回空对象以兼容旧安装。"""
    path = skill_root / INSTALL_METADATA_FILE
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    skill_dir = data.get("dir")
    version = data.get("version")
    if not isinstance(skill_dir, str) or not isinstance(version, str) or not skill_dir or not version:
        return {}
    return {"dir": skill_dir, "version": version}
