"""core 纯逻辑单测示例。运行: python -m pytest tests/（pytest 仅 dev 依赖）"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from core import index, versions
from core.frontmatter import parse_frontmatter


def test_version_compare_numeric_not_lexical():
    assert versions.compare("1.10.0", "1.9.0") == 1
    assert versions.compare("1.0.0", "1.0.0") == 0
    assert versions.compare("0.9.9", "1.0.0") == -1


def test_frontmatter_parse():
    text = "---\nname: demo\ndescription: 测试技能\n---\n\n# 正文\n"
    meta, body = parse_frontmatter(text)
    assert meta == {"name": "demo", "description": "测试技能"}
    assert body.startswith("# 正文")


def test_frontmatter_absent():
    meta, body = parse_frontmatter("# 只有正文")
    assert meta == {}
    assert body == "# 只有正文"


def test_index_filter_and_sort():
    idx = {"skills": [
        {"dir": "a", "name": "alpha", "display_name": "", "description": "",
         "tags": ["code"], "downloads": 5, "likes": 1,
         "versions": [{"version": "1.0.0", "created_at": 100}]},
        {"dir": "b", "name": "beta", "display_name": "测试", "description": "",
         "tags": ["code", "test"], "downloads": 50, "likes": 9,
         "versions": [{"version": "1.0.0", "created_at": 200}]},
    ]}
    assert [s["dir"] for s in index.filter_skills(idx, keyword="测试")] == ["b"]
    assert [s["dir"] for s in index.filter_skills(idx, tags=["test"])] == ["b"]
    assert [s["dir"] for s in index.sort_skills(idx["skills"], "popular")] == ["b", "a"]


def test_has_newer_version():
    skill = {"latest_version": "1.2.0"}
    assert index.has_newer_version(skill, "1.1.0") == "1.2.0"
    assert index.has_newer_version(skill, "1.2.0") is None
