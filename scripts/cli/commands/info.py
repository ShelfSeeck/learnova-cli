"""info：技能详情（元数据 + 版本列表 + README）。"""

from core.errors import CliError


def register(sub) -> None:
    p = sub.add_parser("info", help="技能详情")
    p.add_argument("skill")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    raise CliError("NOT_IMPLEMENTED", "命令 'info' 尚未实现")
