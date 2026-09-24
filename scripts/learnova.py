#!/usr/bin/env python3
"""learnova-cli Router：argparse 构建 + 注册表分发 + 单点异常捕获。

只做三件事，命令实现见 cli/commands/。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import commands, presenter
from cli.context import CliContext
from core.errors import CliError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="learnova-cli", description="Learnova 技能社区命令行入口")
    sub = parser.add_subparsers(dest="command", required=True)
    for module in commands.MODULES:
        module.register(sub)
    return parser


def main() -> int:
    # 允许 --json 出现在子命令前后任意位置
    argv = [a for a in sys.argv[1:] if a != "--json"]
    as_json = len(argv) != len(sys.argv) - 1
    args = build_parser().parse_args(argv)
    args.json = as_json
    ctx = CliContext.build(args)
    try:
        result = args.handler_fn(args, ctx)
        presenter.emit(result, ctx.as_json)
        return 0
    except CliError as e:
        presenter.emit_error(e, ctx.as_json)
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())
