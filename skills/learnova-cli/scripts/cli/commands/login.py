"""login：用户在终端亲自执行，账号密码换 TokenPair 写 auth.json（600）。"""

from core.errors import CliError


def register(sub) -> None:
    sub.add_parser("login", help="登录 Learnova 账号").set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    raise CliError("NOT_IMPLEMENTED", "命令 'login' 尚未实现")
