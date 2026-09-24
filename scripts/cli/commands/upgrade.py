"""upgrade：索引中存在更高版本号才重装。同版本内容不变，不做内容级对比。"""

from core.errors import CliError


def register(sub) -> None:
    p = sub.add_parser("upgrade", help="升级技能")
    p.add_argument("skill", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--project", action="store_true")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    raise CliError("NOT_IMPLEMENTED", "命令 'upgrade' 尚未实现")
