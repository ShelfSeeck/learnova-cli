"""Update the installed Learnova CLI itself."""

import self_update


def register(sub):
    p = sub.add_parser("self-update", help="检查或更新 Learnova CLI 自身")
    p.add_argument("--check", action="store_true", help="仅检查更新")
    p.set_defaults(handler_fn=handler)


def handler(args, ctx):
    return self_update.check() if args.check else self_update.update()
