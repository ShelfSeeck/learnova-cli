"""install：安装技能到各 agent 的 skills 目录。成功后遥测上报（fire-and-forget）。"""

from core.errors import CliError


def register(sub) -> None:
    p = sub.add_parser("install", help="安装技能")
    p.add_argument("skill", help="技能目录名，可用 名称@版本 指定版本")
    p.add_argument("--project", action="store_true", help="安装到项目级而非用户级")
    p.add_argument("--agent", action="append", default=None, help="限定目标 agent，可多次指定")
    p.add_argument("--force", action="store_true", help="覆盖已存在的同名技能")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    raise CliError("NOT_IMPLEMENTED", "命令 'install' 尚未实现")
