"""agent Registry：映射数据表 + 目录检测。

新增 agent = 表中加一行。检测规则：配置根目录存在即视为已安装。
"""

from pathlib import Path

AGENTS = {
    "claude": {
        "detect_dir": ".claude",
        "global_skills": ".claude/skills",
        "project_skills": ".claude/skills",
    },
    "opencode": {
        "detect_dir": ".agents",
        "global_skills": ".agents/skills",
        "project_skills": ".agents/skills",
    },
    "codex": {
        "detect_dir": ".codex",
        "global_skills": ".codex/skills",
        "project_skills": ".agents/skills",
    },
}


def detect(home: Path | None = None) -> list[str]:
    home = home or Path.home()
    return [name for name, spec in AGENTS.items() if (home / spec["detect_dir"]).is_dir()]


def skills_dirs(selected: list[str] | None, project: bool, cwd: Path | None = None) -> dict[str, Path]:
    """解析目标 skills 目录组。selected=None 时取 detect() 的全部结果。"""
    names = selected if selected is not None else detect()
    base = (cwd or Path.cwd()) if project else Path.home()
    key = "project_skills" if project else "global_skills"
    return {name: base / AGENTS[name][key] for name in names}
