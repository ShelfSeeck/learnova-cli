"""SKILL.md frontmatter 解析。纯函数零 IO。

只支持简单的 `key: value` 行，不解析嵌套 YAML（技能 frontmatter 均为扁平结构）。
"""


import json
import re


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
            key, value = pair[1], pair[2].strip()
            if value.startswith('"'):
                value = json.loads(value)
            elif value.startswith("'"):
                if not re.fullmatch(r"'(?:[^']|'')*'", value):
                    raise ValueError('invalid frontmatter')
                value = value[1:-1].replace("''", "'")
            else:
                value = re.split(r'(?:^|[ \t]+)#', value, maxsplit=1)[0].rstrip()
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
    for line in text[3:end].strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    body = text[end + 4:].lstrip("\n")
    return meta, body
