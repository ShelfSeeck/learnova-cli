"""install：安装技能到各 agent 的 skills 目录。成功后遥测上报（fire-and-forget）。"""

import time

import agents
import engine
import telemetry
from core.errors import CliError
from sources import resolver


def register(sub) -> None:
    p = sub.add_parser("install", help="安装技能")
    p.add_argument("skill", help="技能目录名，可用 名称@版本 指定版本")
    p.add_argument("--project", action="store_true", help="安装到项目级而非用户级")
    p.add_argument("--agent", action="append", default=None, help="限定目标 agent，可多次指定")
    p.add_argument("--force", action="store_true", help="覆盖已存在的同名技能")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    skill_dir, version = split_skill_arg(args.skill)
    targets = agents.resolve_targets(
        ctx.selected_agents(args.agent),
        project=ctx.install_project_scope(args.project),
        cwd=ctx.cwd,
    )
    source = resolver.resolve(ctx)
    installed = engine.fetch_and_install(source, skill_dir, version, targets, args.force)
    telemetry.report("install", {"skill": skill_dir, "version": installed[0].version, "ts": time.time()})
    return {
        "name": skill_dir,
        "version": installed[0].version,
        "installed": [vars(s) for s in installed],
        "total": len(installed),
        "source": getattr(source, "name", ""),
    }


def split_skill_arg(value: str) -> tuple[str, str | None]:
    """'名称@版本' → (名称, 版本)；无 @ 时版本为 None。"""
    name, sep, version = value.rpartition("@")
    if not sep:
        return value, None
    if not name or not version:
        raise CliError("BAD_USAGE", f"技能参数格式应为 名称@版本，收到: {value}", exit_code=2)
    return name, version
