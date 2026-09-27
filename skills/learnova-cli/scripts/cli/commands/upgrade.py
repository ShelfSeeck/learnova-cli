"""upgrade：索引中存在更高版本号才重装。同版本内容不变，不做内容级对比。"""

import agents
import engine
from core import index as core_index
from core.errors import CliError
from sources import resolver


def register(sub) -> None:
    p = sub.add_parser("upgrade", help="升级技能")
    p.add_argument("skill", nargs="?", help="技能目录名；省略时需 --all")
    p.add_argument("--all", action="store_true", help="升级全部已安装技能")
    p.add_argument("--project", action="store_true")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    if not args.skill and not args.all:
        raise CliError("BAD_USAGE", "用法: upgrade <技能> 或 upgrade --all", exit_code=2)
    targets = agents.resolve_targets(ctx.selected_agents(), project=bool(args.project), cwd=ctx.cwd)
    installed = engine.scan_installed(targets)
    if args.skill and not any(s.dir == args.skill for s in installed):
        raise CliError("SKILL_NOT_INSTALLED", f"{args.skill} 未安装于所选 agent 目录")

    source = resolver.resolve(ctx)
    catalog = source.search("", [], "popular")
    planned, skipped = core_index.plan_upgrades(
        catalog,
        [{"dir": s.dir, "version": s.version} for s in installed],
        only=args.skill,
    )

    by_dir: dict[str, list[str]] = {}
    for s in installed:
        by_dir.setdefault(s.dir, []).append(s.agent)

    upgraded = []
    for plan in planned:
        agents_for = by_dir[plan["dir"]]
        install_targets = agents.targets_for_agents(targets, set(agents_for))
        engine.fetch_and_install(source, plan["dir"], plan["to"], install_targets, force=True)
        upgraded.append({**plan, "agents": agents_for})
    return {"upgraded": upgraded, "skipped": skipped, "source": getattr(source, "name", "")}
