"""logout：清除本地凭证。"""

import store


def register(sub) -> None:
    sub.add_parser("logout", help="退出登录").set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    store.clear_auth()
    return {"ok": True, "message": "已退出登录"}
