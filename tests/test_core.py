"""core 纯逻辑单测。运行: python -m unittest discover tests -v（零第三方依赖）"""

import sys
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent / "skills" / "learnova-cli" / "scripts"
sys.path.insert(0, str(RUNTIME))

from core import index, versions
from core.frontmatter import parse_frontmatter
from core.errors import CliError
from core.skill import validate_skill_dir


def _skill(dir_name, version="1.0.0", **kw):
    skill = {
        "dir": dir_name,
        "name": dir_name,
        "display_name": "",
        "description": "",
        "tags": [],
        "likes": 0,
        "downloads": 0,
        "latest_version": version,
        "versions": [{"version": version, "created_at": 100}],
    }
    skill.update(kw)
    return skill


class VersionCompareTest(unittest.TestCase):
    def test_numeric_order_not_lexical(self):
        self.assertEqual(versions.compare("1.10.0", "1.9.0"), 1)
        self.assertEqual(versions.compare("1.0.0", "1.0.0"), 0)
        self.assertEqual(versions.compare("0.9.9", "1.0.0"), -1)

    def test_missing_segments_and_prefix(self):
        self.assertEqual(versions.compare("1.1", "1.1.0"), 0)
        self.assertEqual(versions.compare("v1.2.0", "1.2.0"), 0)
        self.assertEqual(versions.compare("2.0", "1.9.9"), 1)


class FrontmatterTest(unittest.TestCase):
    def test_parse_basic(self):
        text = "---\nname: demo\ndescription: 测试技能\n---\n\n# 正文\n"
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta, {"name": "demo", "description": "测试技能"})
        self.assertTrue(body.startswith("# 正文"))

    def test_quoted_values(self):
        meta, _ = parse_frontmatter('---\nname: "demo"\nversion: \'1.0.0\'\n---\n正文')
        self.assertEqual(meta, {"name": "demo", "version": "1.0.0"})

    def test_absent(self):
        meta, body = parse_frontmatter("# 只有正文")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# 只有正文")


class SkillDirTest(unittest.TestCase):
    def test_rejects_path_segments(self):
        self.assertEqual(validate_skill_dir("demo-skill"), "demo-skill")
        for value in ("", ".", "..", "../demo", "a/b", "a\\b"):
            with self.subTest(value=value), self.assertRaises(CliError):
                validate_skill_dir(value)


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.idx = {"skills": [
            _skill("a", name="alpha", tags=["code"], downloads=5, likes=1,
                   versions=[{"version": "1.0.0", "created_at": 100}]),
            _skill("b", name="beta", display_name="测试", tags=["code", "test"], downloads=50, likes=9,
                   versions=[{"version": "1.0.0", "created_at": 200}]),
        ]}

    def test_filter_keyword(self):
        self.assertEqual([s["dir"] for s in index.filter_skills(self.idx, keyword="测试")], ["b"])
        self.assertEqual([s["dir"] for s in index.filter_skills(self.idx, keyword="alpha")], ["a"])
        self.assertEqual(len(index.filter_skills(self.idx, keyword="")), 2)

    def test_filter_tags_require_all(self):
        self.assertEqual([s["dir"] for s in index.filter_skills(self.idx, tags=["test"])], ["b"])
        self.assertEqual([s["dir"] for s in index.filter_skills(self.idx, tags=["code", "test"])], ["b"])
        self.assertEqual(index.filter_skills(self.idx, tags=["nope"]), [])

    def test_sort(self):
        self.assertEqual([s["dir"] for s in index.sort_skills(self.idx["skills"], "popular")], ["b", "a"])
        self.assertEqual([s["dir"] for s in index.sort_skills(self.idx["skills"], "likes")], ["b", "a"])
        self.assertEqual([s["dir"] for s in index.sort_skills(self.idx["skills"], "newest")], ["b", "a"])

    def test_summarize_drops_versions(self):
        summary = index.summarize(self.idx["skills"][0])
        self.assertNotIn("versions", summary)
        self.assertEqual(summary["dir"], "a")
        self.assertEqual(summary["latest_version"], "1.0.0")

    def test_has_newer_version(self):
        skill = {"latest_version": "1.2.0"}
        self.assertEqual(index.has_newer_version(skill, "1.1.0"), "1.2.0")
        self.assertIsNone(index.has_newer_version(skill, "1.2.0"))
        self.assertIsNone(index.has_newer_version(skill, "1.3.0"))

    def test_plan_upgrades(self):
        installed = [{"dir": "a", "version": "0.9.0"}, {"dir": "b", "version": "1.0.0"}, {"dir": "c", "version": "1.0.0"}]
        upgrades, skipped = index.plan_upgrades(self.idx, installed)
        self.assertEqual([u["dir"] for u in upgrades], ["a"])
        self.assertEqual(upgrades[0]["to"], "1.0.0")
        self.assertEqual({s["dir"] for s in skipped}, {"b", "c"})

    def test_plan_upgrades_only(self):
        installed = [{"dir": "a", "version": "0.9.0"}, {"dir": "b", "version": "0.1.0"}]
        upgrades, skipped = index.plan_upgrades(self.idx, installed, only="b")
        self.assertEqual([u["dir"] for u in upgrades], ["b"])
        self.assertEqual(skipped, [])

    def test_plan_upgrades_deduplicates_agents(self):
        installed = [{"dir": "a", "version": "0.9.0"}, {"dir": "a", "version": "0.9.0"}]
        upgrades, _ = index.plan_upgrades(self.idx, installed)
        self.assertEqual([u["dir"] for u in upgrades], ["a"])


if __name__ == "__main__":
    unittest.main()
