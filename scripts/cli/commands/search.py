"""search：搜索社区技能。后端 API 优先，--offline 或后端不可达时走镜像索引缓存。"""

from core.errors import CliError


def register(sub) -> None:
    p = sub.add_parser("search", help="搜索社区技能")
    p.add_argument("keyword")
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--sort", choices=["popular", "newest", "likes"], default="popular")
    p.add_argument("--offline", action="store_true", help="强制使用本地索引缓存")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    raise CliError("NOT_IMPLEMENTED", "命令 'search' 尚未实现")
