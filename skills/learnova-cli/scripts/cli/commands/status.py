"""status：查询已提交版本的审核状态（依赖后端 publish-status 接口）。"""

from core.errors import CliError


def register(sub) -> None:
    p = sub.add_parser("status", help="查询发布审核状态")
    p.add_argument("skill", nargs="?")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    raise CliError("NOT_IMPLEMENTED", "命令 'status' 尚未实现")
