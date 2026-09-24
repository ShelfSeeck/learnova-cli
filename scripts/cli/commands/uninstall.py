"""uninstall：从各 agent skills 目录删除技能。"""

from core.errors import CliError


def register(sub) -> None:
    p = sub.add_parser("uninstall", help="卸载技能")
    p.add_argument("skill")
    p.add_argument("--project", action="store_true")
    p.add_argument("--agent", action="append", default=None)
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    raise CliError("NOT_IMPLEMENTED", "命令 'uninstall' 尚未实现")
