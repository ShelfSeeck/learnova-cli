"""publish：目录打 zip → 上传为仓库条目 → 提交发布进审核流。

version 与 changelog 由用户确认后执行，agent 不代填。
"""

import publishing


def register(sub) -> None:
    p = sub.add_parser("publish", help="发布技能到社区")
    p.add_argument("directory")
    p.add_argument("--version", required=True)
    p.add_argument("--changelog", required=True)
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    return publishing.publish(ctx, args.directory, args.version, args.changelog)
