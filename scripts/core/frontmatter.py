"""SKILL.md frontmatter 解析。纯函数零 IO。

只支持简单的 `key: value` 行，不解析嵌套 YAML（技能 frontmatter 均为扁平结构）。
"""


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """返回 (frontmatter dict, body)。无 frontmatter 时返回 ({}, 原文)。"""
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


def parse_skill_md(path) -> dict:
    """读取 SKILL.md，返回 {name, description, version, ...} 常用字段。"""
    with open(path, encoding="utf-8") as f:
        meta, _ = parse_frontmatter(f.read())
    return meta
