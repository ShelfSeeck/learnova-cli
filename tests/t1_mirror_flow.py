"""T1 镜像链路全流程测试：只依赖本地 stub 与临时目录，不触网、不碰真实 home。

运行: python tests/t1_mirror_flow.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.gitlab_stub import MirrorStub

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "skills" / "learnova-cli" / "scripts" / "learnova.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mirror"


class MirrorFlowTest(unittest.TestCase):
    """按编号顺序执行，步骤间共享 stub 与临时 home 的累积状态。"""

    @classmethod
    def setUpClass(cls):
        cls.stub = MirrorStub(FIXTURE)
        cls.mirror_url = cls.stub.start()
        cls.home = Path(tempfile.mkdtemp(prefix="learnova-t1-home-"))
        (cls.home / ".claude").mkdir()
        (cls.home / ".agents").mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.stub.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def run_cli(self, *args, expect_exit=0) -> dict:
        env = {
            **os.environ,
            "LEARNOVA_HOME": str(self.home / ".learnova"),
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "LEARNOVA_TELEMETRY": "0",
            "PYTHONIOENCODING": "utf-8",
        }
        proc = subprocess.run(
            [sys.executable, str(CLI), *args, "--json"],
            capture_output=True, text=True, encoding="utf-8",
            env=env, cwd=str(REPO_ROOT),
        )
        self.assertEqual(
            proc.returncode, expect_exit,
            f"命令 {' '.join(args)} 退出码 {proc.returncode}，期望 {expect_exit}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        return json.loads(proc.stdout) if proc.stdout.strip() else {}

    # --- 配置 ---

    def test_01_config_set_mirror_url(self):
        payload = self.run_cli("config", "set", "mirror_repo_url", self.mirror_url)
        self.assertEqual(payload["value"], self.mirror_url)
        self.assertEqual(self.run_cli("config", "get")["mirror_repo_url"], self.mirror_url)

    # --- 检索 ---

    def test_02_search(self):
        payload = self.run_cli("search", "demo")
        self.assertEqual(payload["source"], "mirror")
        self.assertEqual(payload["total"], 2)
        self.assertFalse(payload["stale"])
        self.assertEqual(payload["generated_at"], 1727193600.0)
        self.assertEqual({s["dir"] for s in payload["skills"]}, {"demo-search", "demo-notes"})
        self.assertNotIn("versions", payload["skills"][0])

    def test_03_search_sort_and_tag(self):
        before = len(self.stub.requests)
        by_likes = self.run_cli("search", "demo", "--sort", "likes", "--offline")
        self.assertEqual([s["dir"] for s in by_likes["skills"]], ["demo-notes", "demo-search"])
        tagged = self.run_cli("search", "演示", "--tag", "notes", "--offline")
        self.assertEqual([s["dir"] for s in tagged["skills"]], ["demo-notes"])
        self.assertEqual(len(self.stub.requests), before, "--offline 不应访问 GitLab")

    def test_04_info(self):
        payload = self.run_cli("info", "demo-notes")
        self.assertEqual(payload["latest_version"], "1.1.0")
        self.assertEqual([v["version"] for v in payload["versions"]], ["1.0.0", "1.1.0"])
        self.assertIn("fixture 技能的 README", payload["readme"])

    # --- 安装与升级 ---

    def test_05_install_latest(self):
        payload = self.run_cli("install", "demo-notes")
        self.assertEqual(payload["version"], "1.1.0")
        self.assertEqual(payload["total"], 2)
        for base in (self.home / ".claude" / "skills", self.home / ".agents" / "skills"):
            self.assertTrue((base / "demo-notes" / "SKILL.md").is_file())
            self.assertTrue((base / "demo-notes" / "references" / "tips.md").is_file())

    def test_06_install_conflict(self):
        payload = self.run_cli("install", "demo-notes", expect_exit=3)
        self.assertEqual(payload["error"]["code"], "SKILL_CONFLICT")

    def test_07_install_old_version_force(self):
        payload = self.run_cli("install", "demo-notes@1.0.0", "--force")
        self.assertEqual(payload["version"], "1.0.0")
        target = self.home / ".claude" / "skills" / "demo-notes"
        self.assertIn("version: 1.0.0", (target / "SKILL.md").read_text(encoding="utf-8"))
        self.assertFalse((target / "references" / "tips.md").exists())

    def test_08_list(self):
        payload = self.run_cli("list")
        self.assertEqual(payload["total"], 2)
        self.assertEqual({s["agent"] for s in payload["installed"]}, {"claude", "opencode"})
        self.assertTrue(all(s["dir"] == "demo-notes" for s in payload["installed"]))
        self.assertTrue(all(s["version"] == "1.0.0" for s in payload["installed"]))

    def test_09_upgrade(self):
        payload = self.run_cli("upgrade", "demo-notes")
        self.assertEqual(payload["upgraded"][0]["from"], "1.0.0")
        self.assertEqual(payload["upgraded"][0]["to"], "1.1.0")
        target = self.home / ".claude" / "skills" / "demo-notes"
        self.assertTrue((target / "references" / "tips.md").is_file())

    def test_10_upgrade_noop(self):
        payload = self.run_cli("upgrade", "demo-notes")
        self.assertEqual(payload["upgraded"], [])
        self.assertEqual(payload["skipped"][0]["reason"], "已是最新")

    # --- 卸载与错误路径 ---

    def test_11_uninstall(self):
        payload = self.run_cli("uninstall", "demo-notes")
        self.assertEqual(payload["total"], 2)
        self.assertFalse((self.home / ".claude" / "skills" / "demo-notes").exists())
        self.assertFalse((self.home / ".agents" / "skills" / "demo-notes").exists())

    def test_12_uninstall_absent(self):
        payload = self.run_cli("uninstall", "demo-notes", expect_exit=1)
        self.assertEqual(payload["error"]["code"], "SKILL_NOT_INSTALLED")

    def test_13_install_unknown_skill(self):
        payload = self.run_cli("install", "demo-unknown", expect_exit=1)
        self.assertEqual(payload["error"]["code"], "SKILL_NOT_FOUND")

    def test_14_install_unknown_version(self):
        payload = self.run_cli("install", "demo-notes@9.9.9", expect_exit=1)
        self.assertEqual(payload["error"]["code"], "VERSION_NOT_FOUND")

    def test_15_only_mirror_endpoints_called(self):
        for path in self.stub.requests:
            self.assertTrue(
                "/raw/main/" in path or "/repository/archive.tar.gz" in path,
                f"stub 收到非预期请求: {path}",
            )

    def test_16_uninstall_rejects_path_traversal(self):
        payload = self.run_cli("uninstall", "../../victim", expect_exit=2)
        self.assertEqual(payload["error"]["code"], "INVALID_SKILL_DIR")


if __name__ == "__main__":
    unittest.main(verbosity=2)
