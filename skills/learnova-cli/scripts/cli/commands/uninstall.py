"""uninstall：从各 agent skills 目录删除技能。"""

import agents
import engine


def register(sub) -> None:
    p = sub.add_parser("uninstall", help="卸载技能")
    p.add_argument("skill")
    p.add_argument("--project", action="store_true")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    targets = agents.management_targets(ctx.selected_agents(), project=bool(args.project), cwd=ctx.cwd)
    removed = engine.uninstall(args.skill, targets)
    return {"name": args.skill, "removed": removed, "total": len(removed)}
