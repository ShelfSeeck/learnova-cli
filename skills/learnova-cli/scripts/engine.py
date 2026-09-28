"""安装引擎：install/list/uninstall/upgrade 共享的领域服务。

命令层零文件操作，全部经由这里。
"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agents import AgentTarget
from core.errors import CliError, ConflictError
from core.frontmatter import parse_frontmatter
from core.skill import parse_skill_meta, read_install_metadata, validate_skill_dir

STAGING_PREFIX = ".learnova-staging-"
BACKUP_PREFIX = ".learnova-backup-"


@dataclass
class InstalledSkill:
    dir: str
    name: str
    version: str
    agent: str
    path: str


def _skill_target(base: Path, name: str) -> Path:
    validate_skill_dir(name)
    root = base.resolve(strict=False)
    target = (base / name).resolve(strict=False)
    if not target.is_relative_to(root):
        raise CliError("INVALID_SKILL_DIR", f"技能路径越出 skills 根目录: {name}", exit_code=2)
    return base / name


def _read_skill_meta(skill_files: Path) -> dict:
    skill_md = skill_files / "SKILL.md"
    if not skill_md.is_file() or skill_md.is_symlink():
        raise CliError("SKILL_PACKAGE_INVALID", "技能包缺少普通文件 SKILL.md")
    try:
        meta = parse_skill_meta(skill_md.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError("SKILL_PACKAGE_INVALID", "无法读取技能包 SKILL.md") from exc
    install_meta = read_install_metadata(skill_files)
    if install_meta.get("version"):
        meta = {**meta, "version": install_meta["version"]}
    if not meta.get("version"):
        raise CliError("SKILL_PACKAGE_INVALID", "技能包缺少可识别的版本信息")
    return meta


def _remove_generated(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def scan_installed(targets: list[AgentTarget]) -> list[InstalledSkill]:
    """扫描 agent skills 目录，从 SKILL.md frontmatter 重建已安装清单。

    跳过隐藏目录（引擎自己的临时/备份目录）。
    """
    found = []
    for target in targets:
        skills_dir = target.path
        if not skills_dir.is_dir():
            continue
        for child in sorted(skills_dir.iterdir()):
            if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                meta, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            install_meta = read_install_metadata(child)
            installed_version = (
                install_meta.get("version")
                if install_meta.get("dir") == child.name
                else meta.get("version", "")
            )
            for agent in target.agents:
                found.append(InstalledSkill(
                    dir=child.name,
                    name=meta.get("name", child.name),
                    version=installed_version,
                    agent=agent,
                    path=str(child),
                ))
    return found


def install(skill_files: Path, targets: list[AgentTarget], name: str, force: bool) -> list[InstalledSkill]:
    """验证、stage 所有目标，再提交；提交失败时回滚已替换目标。"""
    validate_skill_dir(name)
    meta = _read_skill_meta(skill_files)
    if not targets:
        raise CliError("NO_AGENT_FOUND", "未检测到任何 agent 目标目录，可用 --agent 指定")

    resolved: list[tuple[AgentTarget, Path]] = []
    for target in targets:
        final = _skill_target(target.path, name)
        if final.is_symlink():
            raise CliError("SKILL_CONFLICT", f"目标路径是符号链接: {final}", exit_code=3)
        resolved.append((target, final))

    if (conflicts := [str(final) for _, final in resolved if final.exists()]) and not force:
        raise ConflictError(
            "SKILL_CONFLICT",
            f"{name} 已存在于: {', '.join(conflicts)}；使用 --force 覆盖",
        )

    staged: list[tuple[AgentTarget, Path, Path, Path]] = []
    for target, final in resolved:
        base = target.path
        base.mkdir(parents=True, exist_ok=True)
        staging = base / f"{STAGING_PREFIX}{name}"
        backup = base / f"{BACKUP_PREFIX}{name}"
        _remove_generated(staging)
        _remove_generated(backup)
        shutil.copytree(skill_files, staging)
        staged.append((target, staging, final, backup))

    try:
        for _, staging, final, backup in staged:
            if final.exists():
                os.replace(final, backup)
            os.replace(staging, final)
    except Exception:
        for _, staging, final, backup in reversed(staged):
            if backup.exists():
                if final.exists():
                    _remove_generated(final)
                os.replace(backup, final)
            elif not staging.exists() and final.exists():
                _remove_generated(final)
            _remove_generated(staging)
        raise
    for _, _, _, backup in staged:
        _remove_generated(backup)

    return [
        InstalledSkill(dir=name, name=meta.get("name", name), version=meta.get("version", ""),
                       agent=agent, path=str(final))
        for target, _, final, _ in staged
        for agent in target.agents
    ]


def fetch_and_install(source, skill_dir: str, version: str | None,
                      targets: list[AgentTarget], force: bool) -> list[InstalledSkill]:
    """安装临时工作目录由 engine 统一管理，command 不直接操作文件系统。"""
    with tempfile.TemporaryDirectory(prefix="learnova-fetch-") as tmp:
        files = source.fetch(skill_dir, version, Path(tmp))
        return install(files, targets, skill_dir, force)


def uninstall(name: str, targets: list[AgentTarget]) -> list[dict]:
    """删除各目标目录中的技能，全部不存在时报错。"""
    validate_skill_dir(name)
    removed = []
    for target in targets:
        final = _skill_target(target.path, name)
        if final.is_symlink():
            raise CliError("SKILL_CONFLICT", f"拒绝删除符号链接技能目录: {final}", exit_code=3)
        if final.is_dir():
            shutil.rmtree(final)
            removed.extend({"agent": agent, "path": str(final)} for agent in target.agents)
    if not removed:
        raise CliError("SKILL_NOT_INSTALLED", f"{name} 未安装于所选 agent 目录")
    return removed
