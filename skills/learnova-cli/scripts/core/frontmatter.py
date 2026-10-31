"""SKILL.md frontmatter 解析。纯函数零 IO。

读取顶层简单字段；非严格模式忽略嵌套键，严格模式只接受扁平字符串。
"""


import json
import re


def _scalar(value: str) -> tuple[str, bool]:
    if value.startswith('"'):
        decoded, end = json.JSONDecoder().raw_decode(value)
        trailing = value[end:].strip()
        if trailing and not trailing.startswith('#'):
            raise ValueError('invalid frontmatter')
        return decoded, True
    if value.startswith("'"):
        quoted = re.fullmatch(r"'((?:[^']|'')*)'(?:[ \t]+#.*)?", value)
        if not quoted:
            raise ValueError('invalid frontmatter')
        return quoted[1].replace("''", "'"), True
    return re.split(r'(?:^|[ \t]+)#', value, maxsplit=1)[0].rstrip(), False


def parse_frontmatter(text: str, *, strict=False) -> tuple[dict, str]:
    """返回 (frontmatter dict, body)；strict 校验扁平字符串字段并解码引号。"""
    if strict:
        match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.S)
        if not match:
            raise ValueError('invalid frontmatter')
        meta = {}
        for line in match.group(1).split('\n'):
            line = line.removesuffix('\r')
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            pair = re.fullmatch(r'([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)', line)
            if not pair or pair[1] in meta:
                raise ValueError('invalid frontmatter')
            key = pair[1]
            value, quoted = _scalar(pair[2].strip())
            if not quoted:
                if (not value or value[0] in '[{>|&*!' or ': ' in value
                        or value.lower() in ('null', '~', 'true', 'false', '.inf', '-.inf', '+.inf', '.nan')
                        or re.fullmatch(r'[+-]?(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9_]+)(?:[eE][+-]?[0-9]+)?', value)
                        or re.fullmatch(r'0[xob][0-9a-fA-F_]+', value)):
                    raise ValueError('invalid frontmatter')
            if not isinstance(value, str):
                raise ValueError('invalid frontmatter')
            meta[key] = value
        return meta, text[match.end():]
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta = {}
    lines = text[3:end].splitlines()
    fields = [line for line in lines if line.strip() and not line.lstrip().startswith('#') and ':' in line]
    root_indent = min((len(line) - len(line.lstrip()) for line in fields), default=0)
    for line in fields:
        if len(line) - len(line.lstrip()) != root_indent:
            continue
        key, _, value = line.partition(":")
        try:
            decoded, _ = _scalar(value.strip())
        except ValueError:
            decoded = value.strip().strip('"').strip("'")
        meta[key.strip()] = decoded
    body = text[end + 4:].lstrip("\n")
    return meta, body
