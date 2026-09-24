"""whoami：查看当前登录状态。"""

import store


def register(sub) -> None:
    sub.add_parser("whoami", help="查看当前账号").set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    auth = store.load_auth()
    return {
        "logged_in": bool(auth.get("refresh_token")),
        "username": auth.get("username"),
    }
