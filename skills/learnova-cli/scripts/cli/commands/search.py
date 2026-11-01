"""search：搜索社区技能。M2 后端未配置时走镜像；--offline 严格只读本地缓存。"""

from sources import resolver


def register(sub) -> None:
    p = sub.add_parser("search", help="搜索社区技能")
    p.add_argument("keyword")
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--sort", choices=["popular", "newest", "likes"], default="popular")
    p.add_argument("--offline", action="store_true", help="严格离线，只读本地索引缓存")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    source = resolver.resolve(ctx)
    return source.search(args.keyword, args.tag, args.sort)
