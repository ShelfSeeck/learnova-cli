"""Opt-in real mirror acceptance, isolated from the operator's home and skills."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills/learnova-cli/scripts/learnova.py"


def run(mirror_url=None):
    with tempfile.TemporaryDirectory(prefix="learnova-mirror-smoke-") as temp:
        home = Path(temp)
        (home / ".codex").mkdir()
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG") if key in os.environ}
        env.update(HOME=temp, USERPROFILE=temp, TMPDIR=temp, TEMP=temp, TMP=temp,
                   LEARNOVA_HOME=str(home / "state"), LEARNOVA_TELEMETRY="0", PYTHONIOENCODING="utf-8")

        def cli(*args):
            result = subprocess.run([sys.executable, str(CLI), *args, "--json"], env=env, cwd=temp,
                                    capture_output=True, text=True, encoding="utf-8", timeout=180)
            try:
                data = json.loads(result.stdout)
            except ValueError:
                raise RuntimeError("CLI did not return valid JSON") from None
            if result.returncode:
                raise RuntimeError("CLI operation failed: " + str(data.get("error", {}).get("code", "UNKNOWN")))
            return data

        if mirror_url:
            cli("config", "set", "mirror_repo_url", mirror_url)
        results = cli("search", "")
        if results.get("source") != "mirror" or not results.get("total"):
            raise RuntimeError("mirror returned no installable skills")
        # Pick a current small package instead of depending on a named skill's continued availability.
        index = json.loads((home / "state/index_cache.json").read_text(encoding="utf-8"))["index"]
        candidates = []
        for skill in index["skills"]:
            latest = next(item for item in skill["versions"] if item["version"] == skill["latest_version"])
            candidates.append((latest.get("size_bytes", 0), skill["dir"], skill["latest_version"]))
        _, name, version = min(candidates)
        detail = cli("info", name)
        if detail.get("latest_version") != version:
            raise RuntimeError("mirror detail does not match its index")
        installed = cli("install", name + "@" + version, "--agent", "codex")
        if installed.get("version") != version or installed.get("total") != 1:
            raise RuntimeError("mirror did not install the pinned version")
        target = Path(installed["installed"][0]["path"])
        if not target.resolve().is_relative_to(home.resolve()) or not (target / "SKILL.md").is_file():
            raise RuntimeError("installation did not stay inside the temporary home")
        count = sum(path.is_file() for path in target.rglob("*"))
        listing = cli("list")
        if not any(item["dir"] == name and item["version"] == version for item in listing["installed"]):
            raise RuntimeError("installed version was not listed")
        cli("uninstall", name)
        if target.exists() or cli("list")["total"] != 0:
            raise RuntimeError("temporary installation was not removed")
        return {"status": "passed", "catalog_total": results["total"], "installed_files": count,
                "pinned_version": True, "cleanup": True}


def main():
    if os.environ.get("LEARNOVA_RUN_MIRROR_SMOKE") != "1":
        print(json.dumps({"status": "not_run", "reason": "set LEARNOVA_RUN_MIRROR_SMOKE=1"}))
        return 2
    try:
        result = run(os.environ.get("LEARNOVA_TEST_MIRROR_URL"))
    except Exception:
        print(json.dumps({"status": "failed", "reason": "mirror acceptance failed"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
