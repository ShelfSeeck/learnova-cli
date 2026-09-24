"""安装引擎：install/list/uninstall/upgrade 共享的领域服务。

命令层零文件操作，全部经由这里。
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from core.errors import CliError, ConflictError
from core.frontmatter import parse_skill_md


@dataclass
class InstalledSkill:
    name: str
    version: str
    agent: str
    path: str


def scan_installed(target_dirs: dict[str, Path]) -> list[InstalledSkill]:
    """扫描 agent skills 目录，从 SKILL.md frontmatter 重建已安装清单。"""
    found = []
    for agent, skills_dir in target_dirs.items():
        if not skills_dir.is_dir():
            continue
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if not child.is_dir() or not skill_md.is_file():
                continue
            meta = parse_skill_md(skill_md)
            found.append(InstalledSkill(
                name=meta.get("name", child.name),
                version=meta.get("version", ""),
                agent=agent,
                path=str(child),
            ))
    return found


def install(skill_files: Path, target_dirs: dict[str, Path], name: str, force: bool) -> list[Path]:
    """原子拷贝：先临时目录再 rename。中断不留半截坏技能。"""
    raise CliError("NOT_IMPLEMENTED", "engine.install 尚未实现")


def uninstall(name: str, target_dirs: dict[str, Path]) -> list[Path]:
    raise CliError("NOT_IMPLEMENTED", "engine.uninstall 尚未实现")
