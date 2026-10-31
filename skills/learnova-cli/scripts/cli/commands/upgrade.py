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
    targets = agents.management_targets(ctx.selected_agents(), project=bool(args.project), cwd=ctx.cwd)
    installed = engine.scan_installed(targets)
    if args.skill and not any(s.dir == args.skill for s in installed):
        raise CliError("SKILL_NOT_INSTALLED", f"{args.skill} 未安装于所选 agent 目录")

    eligible = [s for s in installed if s.source == "community" and (not args.skill or s.dir == args.skill)]
    skipped = [{"dir": s.dir, "path": s.path, "reason": f"来源为 {s.source}"}
               for s in installed if s.source != "community" and (not args.skill or s.dir == args.skill)]
    if not eligible:
        return {"upgraded": [], "skipped": skipped, "source": ""}
    source = resolver.resolve(ctx)
    catalog = source.search("", [], "popular")
    upgraded = []
    # A directory can be shared by logical agents; plan and install it only once.
    by_path = {}
    for item in eligible:
        by_path.setdefault(item.path, []).append(item)
    for path, items in by_path.items():
        item = items[0]
        planned, rejected = core_index.plan_upgrades(catalog, [{"dir": item.dir, "version": item.version}])
        skipped.extend({**entry, "path": path} for entry in rejected)
        if planned:
            target = next(t for t in targets if str(t.path / item.dir) == path)
            engine.fetch_and_install(source, item.dir, planned[0]["to"], [target], force=True)
            upgraded.append({**planned[0], "agents": [s.agent for s in items]})
    return {"upgraded": upgraded, "skipped": skipped, "source": getattr(source, "name", "")}
