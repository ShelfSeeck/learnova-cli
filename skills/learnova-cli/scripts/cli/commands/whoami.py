"""whoami：查看当前登录状态。"""

import auth


def register(sub) -> None:
    sub.add_parser("whoami", help="查看当前账号").set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    return auth.whoami(ctx)
