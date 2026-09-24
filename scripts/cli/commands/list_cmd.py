"""list：扫描 agent skills 目录重建已安装清单（不依赖本地记录）。"""

import agents
import engine


def register(sub) -> None:
    p = sub.add_parser("list", help="列出已安装技能")
    p.add_argument("--project", action="store_true")
    p.add_argument("--agent", action="append", default=None)
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    target_dirs = agents.skills_dirs(args.agent, project=args.project, cwd=ctx.cwd)
    installed = engine.scan_installed(target_dirs)
    return {
        "installed": [vars(s) for s in installed],
        "total": len(installed),
        "scope": "project" if args.project else "user",
    }
