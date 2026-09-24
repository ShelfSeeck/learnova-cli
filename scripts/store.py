"""本地状态：~/.learnova/ 下的 JSON 读写。

- config.json：配置项（见 DEFAULTS）
- auth.json：登录凭证，权限 600
- index_cache.json：社区索引离线缓存

写盘一律临时文件 + os.replace 原子替换。
"""

import json
import os
import tempfile
import time
from pathlib import Path

LEARNOVA_HOME = Path(os.environ.get("LEARNOVA_HOME", Path.home() / ".learnova"))
CONFIG_PATH = LEARNOVA_HOME / "config.json"
AUTH_PATH = LEARNOVA_HOME / "auth.json"
INDEX_CACHE_PATH = LEARNOVA_HOME / "index_cache.json"

DEFAULTS = {
    "backend_url": "",          # 发行时填入默认值
    "mirror_repo_url": "",      # 发行时填入默认值
    "telemetry": "auto",        # auto=跟随登录态 / off=关闭
    "agents": "auto",           # auto=检测，或 ["claude", "codex"] 子集
    "install_scope": "user",    # user / project
    "index_cache_ttl": 86400,   # 秒
}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    os.chmod(path, mode)


def load_config() -> dict:
    return {**DEFAULTS, **_read_json(CONFIG_PATH)}


def set_config_value(key: str, value: str) -> None:
    from core.errors import CliError
    if key not in DEFAULTS:
        raise CliError("BAD_CONFIG_KEY", f"未知配置项: {key}，可选: {', '.join(DEFAULTS)}", exit_code=2)
    data = _read_json(CONFIG_PATH)
    data[key] = value
    _write_json(CONFIG_PATH, data)


def load_auth() -> dict:
    return _read_json(AUTH_PATH)


def save_auth(data: dict) -> None:
    _write_json(AUTH_PATH, data)


def clear_auth() -> None:
    AUTH_PATH.unlink(missing_ok=True)


def load_index_cache() -> tuple[dict | None, bool]:
    """返回 (index 或 None, 是否超过 TTL)。"""
    raw = _read_json(INDEX_CACHE_PATH)
    if not raw:
        return None, True
    stale = (time.time() - raw.get("fetched_at", 0)) > load_config()["index_cache_ttl"]
    return raw.get("index"), stale


def save_index_cache(index: dict) -> None:
    _write_json(INDEX_CACHE_PATH, {"fetched_at": time.time(), "index": index})


def telemetry_enabled() -> bool:
    if os.environ.get("LEARNOVA_TELEMETRY") == "0":
        return False
    if load_config()["telemetry"] == "off":
        return False
    return bool(load_auth().get("refresh_token"))
