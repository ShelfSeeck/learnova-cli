"""info：技能详情（元数据 + 版本列表 + README）。"""

from sources import resolver


def register(sub) -> None:
    p = sub.add_parser("info", help="技能详情")
    p.add_argument("skill")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    source = resolver.resolve(ctx)
    return source.info(args.skill)
