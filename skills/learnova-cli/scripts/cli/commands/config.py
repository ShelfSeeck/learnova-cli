"""config：查看/修改本地配置。"""

import store


def register(sub) -> None:
    p = sub.add_parser("config", help="查看/修改配置")
    p.add_argument("config_action", choices=["get", "set"], nargs="?", default="get")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    if args.config_action == "set":
        if not args.key or args.value is None:
            from core.errors import CliError
            raise CliError("BAD_USAGE", "用法: config set <键> <值>", exit_code=2)
        value = store.set_config_value(args.key, args.value)
        return {"ok": True, "key": args.key, "value": value}
    return store.load_config()
