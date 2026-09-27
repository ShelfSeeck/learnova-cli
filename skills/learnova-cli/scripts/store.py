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

from core.errors import CliError

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
CONFIG_CHOICES = {
    "telemetry": {"auto", "off"},
    "install_scope": {"user", "project"},
}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError("LOCAL_STATE_INVALID", f"无法读取本地状态文件: {path.name}") from exc
    if not isinstance(data, dict):
        raise CliError("LOCAL_STATE_INVALID", f"本地状态文件必须是 JSON 对象: {path.name}")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _validate_config(config: dict, code: str, exit_code: int = 1) -> dict:
    for key, choices in CONFIG_CHOICES.items():
        if config[key] not in choices:
            raise CliError(code, f"配置 {key} 只能是 {', '.join(sorted(choices))}", exit_code=exit_code)
    agents = config["agents"]
    if agents != "auto" and (
        not isinstance(agents, list)
        or not agents
        or not all(isinstance(item, str) and item for item in agents)
    ):
        raise CliError(code, "配置 agents 必须是 auto 或非空字符串数组", exit_code=exit_code)
    ttl = config["index_cache_ttl"]
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0:
        raise CliError(code, "配置 index_cache_ttl 必须是正整数", exit_code=exit_code)
    return config


def load_config() -> dict:
    return _validate_config({**DEFAULTS, **_read_json(CONFIG_PATH)}, "LOCAL_STATE_INVALID")


def _parse_config_value(key: str, value: str):
    if key == "agents":
        if value == "auto":
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise CliError("BAD_USAGE", "agents 需要 JSON 数组，如 [\"claude\", \"codex\"]", exit_code=2) from exc
    if isinstance(DEFAULTS[key], int):
        return int(value) if value.isdigit() else value
    return value


def set_config_value(key: str, value: str):
    if key not in DEFAULTS:
        raise CliError("BAD_CONFIG_KEY", f"未知配置项: {key}，可选: {', '.join(DEFAULTS)}", exit_code=2)
    value = _parse_config_value(key, value)
    data = _read_json(CONFIG_PATH)
    data[key] = value
    _validate_config({**DEFAULTS, **data}, "BAD_USAGE", exit_code=2)
    _write_json(CONFIG_PATH, data)
    return value


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
    fetched_at = raw.get("fetched_at")
    index = raw.get("index")
    if not isinstance(fetched_at, (int, float)) or not isinstance(index, dict):
        raise CliError("LOCAL_STATE_INVALID", "index_cache.json 结构非法")
    stale = (time.time() - fetched_at) > load_config()["index_cache_ttl"]
    return index, stale


def save_index_cache(index: dict) -> None:
    _write_json(INDEX_CACHE_PATH, {"fetched_at": time.time(), "index": index})


def telemetry_enabled() -> bool:
    return (
        os.environ.get("LEARNOVA_TELEMETRY") != "0"
        and load_config()["telemetry"] != "off"
        and bool(load_auth().get("refresh_token"))
    )
