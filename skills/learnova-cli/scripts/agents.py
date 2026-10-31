"""agent Registry：映射数据表 + 目录检测。

新增 agent = 表中加一行。检测规则：配置根目录存在即视为已安装。
"""

from dataclasses import dataclass
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


@dataclass(frozen=True)
class AgentTarget:
    """一个物理 skills 目录及共享该目录的逻辑 agent。"""
    path: Path
    agents: tuple[str, ...]


def detect(home: Path | None = None) -> list[str]:
    home = home or Path.home()
    return [name for name, spec in AGENTS.items() if (home / spec["detect_dir"]).is_dir()]


def resolve_targets(
    selected: list[str] | None,
    project: bool,
    cwd: Path | None = None,
    home: Path | None = None,
) -> list[AgentTarget]:
    """将逻辑 agent 映射为去重后的物理 InstallTarget。"""
    if selected:
        unknown = [n for n in selected if n not in AGENTS]
        if unknown:
            from core.errors import CliError
            raise CliError("BAD_USAGE", f"未知 agent: {', '.join(unknown)}，可选: {', '.join(AGENTS)}", exit_code=2)
    names = selected if selected else detect(home)
    base = (cwd or Path.cwd()) if project else (home or Path.home())
    key = "project_skills" if project else "global_skills"
    grouped: dict[Path, list[str]] = {}
    for name in names:
        path = base / AGENTS[name][key]
        grouped.setdefault(path, []).append(name)
    return [AgentTarget(path=path, agents=tuple(agent_names)) for path, agent_names in grouped.items()]


def targets_for_agents(targets: list[AgentTarget], names: set[str]) -> list[AgentTarget]:
    """保留包含指定逻辑 agent 的物理目标。"""
    return [target for target in targets if any(name in names for name in target.agents)]


def management_targets(selected, project, cwd=None, home=None):
    """Management includes the canonical project root and deduplicates physical paths."""
    targets = resolve_targets(selected, project, cwd, home)
    if project:
        targets.append(AgentTarget((cwd or Path.cwd()) / ".agents/skills", ("project",)))
    grouped = {}
    for target in targets:
        key = target.path.resolve(strict=False)
        if key not in grouped:
            grouped[key] = target
        elif target.agents != ("project",):
            prior = grouped[key]
            grouped[key] = AgentTarget(prior.path, tuple(dict.fromkeys(prior.agents + target.agents)))
    return list(grouped.values())
