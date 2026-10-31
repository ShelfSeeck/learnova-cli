"""list：扫描 agent skills 目录重建已安装清单（不依赖本地记录）。"""

import agents
import engine


def register(sub) -> None:
    p = sub.add_parser("list", help="列出已安装技能")
    p.add_argument("--project", action="store_true")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    project = bool(args.project)
    targets = agents.management_targets(ctx.selected_agents(), project=project, cwd=ctx.cwd)
    installed = engine.scan_installed(targets)
    return {
        "installed": [vars(s) for s in installed],
        "total": len(installed),
        "scope": "project" if project else "user",
    }
