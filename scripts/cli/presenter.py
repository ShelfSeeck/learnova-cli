"""Presenter：唯一输出出口。

handler 返回的 dict 是契约；人类可读格式只是渲染，可随意调整。
"""

import json

from core.errors import CliError


def emit(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(_human(data))


def emit_error(exc: CliError, as_json: bool) -> None:
    import sys
    payload = {"error": {"code": exc.code, "message": exc.message}}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"错误 [{exc.code}]: {exc.message}", file=sys.stderr)


def _human(value, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(_human(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{pad}(空)"
        return "\n".join(_human(item, indent) for item in value)
    return f"{pad}{value}"
