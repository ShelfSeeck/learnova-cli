"""Lightweight CLI update from the official NJU GitLab repository."""

import os
import re
import subprocess
import tempfile
from pathlib import Path

import engine
import net
from core.errors import CliError
from core.skill import parse_skill_meta
from core.versions import compare

REPO = "https://git.nju.edu.cn/learnova/learnova-cli.git"
RAW_VERSION = "https://git.nju.edu.cn/learnova/learnova-cli/-/raw/main/skills/learnova-cli/VERSION"
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")


def skill_root():
    return Path(__file__).resolve().parent.parent


def read_version(root):
    path = root / "VERSION"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise CliError("SELF_UPDATE_INVALID", "无效的 VERSION 文件")
    value = path.read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise CliError("SELF_UPDATE_INVALID", "VERSION 必须为 x.y.z")
    return value


def check(root=None):
    root = root or skill_root()
    current = read_version(root)
    remote = net.get_text(RAW_VERSION, timeout=5, max_bytes=128)
    if remote is None or not VERSION_PATTERN.fullmatch(remote.strip()):
        raise CliError("SELF_UPDATE_INVALID", "官方仓库版本号无效")
    latest = remote.strip()
    available = current is None or compare(latest, current) > 0
    return {"status": "update_available" if available else "up_to_date",
            "current_version": current, "latest_version": latest,
            "update_available": available}


def update(root=None):
    root = root or skill_root()
    current = read_version(root)
    with tempfile.TemporaryDirectory(prefix="learnova-self-update-") as temp:
        checkout = Path(temp) / "repository"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", "main", "--single-branch",
                 "--quiet", REPO, str(checkout)],
                check=True, capture_output=True, timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
        except (OSError, subprocess.SubprocessError) as exc:
            raise CliError("SELF_UPDATE_FAILED", "从 NJU GitLab 下载失败") from exc
        candidate = checkout / "skills" / "learnova-cli"
        if not (candidate / "scripts" / "learnova.py").is_file():
            raise CliError("SELF_UPDATE_INVALID", "下载的 CLI 缺少入口")
        if any(item.is_symlink() for item in candidate.rglob("*")):
            raise CliError("SELF_UPDATE_INVALID", "下载的 CLI 包含符号链接")
        latest = read_version(candidate)
        if latest is None or parse_skill_meta((candidate / "SKILL.md").read_text(encoding="utf-8")).get("name") != "learnova-cli":
            raise CliError("SELF_UPDATE_INVALID", "下载的 CLI 包结构无效")
        if current is not None and compare(latest, current) <= 0:
            return {"status": "up_to_date", "current_version": current, "latest_version": latest}
        engine.commit_directories(candidate, [root], force=True)
    return {"status": "updated", "previous_version": current, "version": latest, "path": str(root)}
