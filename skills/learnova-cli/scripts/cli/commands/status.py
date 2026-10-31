"""status：查询当前账号提交版本的审核状态。"""

import publishing


def register(sub) -> None:
    p = sub.add_parser("status", help="查询发布审核状态")
    p.add_argument("skill", nargs="?")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    return publishing.status(ctx, args.skill)
