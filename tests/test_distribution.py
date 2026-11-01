"""A copied distribution must work without the repository or caller's Python path."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

PACKAGE = Path(__file__).resolve().parents[1] / "skills/learnova-cli"


class DistributionTests(unittest.TestCase):
    def test_runtime_reference_links_are_shipped(self):
        for document in [PACKAGE / "SKILL.md", *sorted((PACKAGE / "references").glob("*.md"))]:
            for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", document.read_text(encoding="utf-8")):
                if urlsplit(target).scheme or target.startswith("#"):
                    continue
                with self.subTest(document=document.name, target=target):
                    resolved = (document.parent / unquote(target.split("#", 1)[0])).resolve()
                    self.assertTrue(resolved.is_relative_to(PACKAGE.resolve()))
                    self.assertTrue(resolved.is_file())

    def test_copied_runtime_runs_from_all_agent_layouts(self):
        with tempfile.TemporaryDirectory(prefix="learnova-distribution-") as temp:
            root = Path(temp)
            home = root / "home"
            home.mkdir()
            env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG") if key in os.environ}
            env.update(HOME=str(home), USERPROFILE=str(home), LEARNOVA_HOME=str(home / "state"),
                       LEARNOVA_TELEMETRY="0", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
            for agent, layout in (("codex", ".agents"), ("opencode", ".agents"), ("claude-code", ".claude")):
                with self.subTest(agent=agent):
                    project = root / agent
                    project.mkdir()
                    installed = project / layout / "skills/learnova-cli"
                    shutil.copytree(PACKAGE, installed, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

                    def command(*args, expected=0):
                        result = subprocess.run([sys.executable, "-B", str(installed / "scripts/learnova.py"), *args, "--json"],
                                                cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
                        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                        self.assertEqual(result.stderr, "")
                        return json.loads(result.stdout)

                    self.assertIn("mirror_repo_url", command("config"))
                    self.assertEqual(command("unknown-command", expected=2)["error"]["code"], "BAD_USAGE")
                    # Exercise lazy build imports and bundled decision rules from the installed path.
                    source = project / "source.md"
                    source.write_text("# Queue\n\nA queue stores items in arrival order.\n", encoding="utf-8")
                    task = command("build", "create", str(source), "--project-root", str(project))["task_id"]
                    state = command("build", "next", task, "--project-root", str(project))
                    self.assertEqual(state["status"], "needs_input")
                    self.assertEqual(state["request"]["kind"], "split_plan")


if __name__ == "__main__":
    unittest.main()
