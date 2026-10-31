"""logout：清除本地凭证。"""

import auth


def register(sub) -> None:
    sub.add_parser("logout", help="退出登录").set_defaults(handler_fn=handler)


def handler(args, ctx) -> dict:
    return auth.logout(ctx)
