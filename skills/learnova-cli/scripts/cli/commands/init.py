"""init：首次初始化引导（幂等）。交互命令，由用户在终端亲自执行。"""

import bootstrap


def register(sub) -> None:
    sub.add_parser("init", help="初始化配置").set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    return bootstrap.initialize(ctx)
