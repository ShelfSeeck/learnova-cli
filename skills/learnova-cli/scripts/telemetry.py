"""遥测：fire-and-forget。失败静默，永不阻塞主流程。

只在 store.telemetry_enabled() 为真时上报（默认跟随登录态）。
"""

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import re
from urllib.parse import urlencode

import auth
import store
from cli.context import CliContext
from core.index import validate_index

_workers = []


def _payload(payload):
    from core.skill import validate_skill_dir
    skill = validate_skill_dir(payload["skill"])
    version, timestamp = payload["version"], payload["ts"]
    if (not isinstance(version, str) or not version or len(version) > 200
            or not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
            or not math.isfinite(timestamp) or timestamp < 0):
        raise ValueError("invalid event")
    return {"skill": skill, "version": version, "ts": timestamp}


def report(event: str, payload: dict, *, ctx=None) -> None:
    """异步上报事件，任何异常静默吞掉。"""
    try:
        if event != "install" or not store.telemetry_enabled():
            return
        config = store.load_config()
        if (not config.get("telemetry_endpoint") or ctx is None or not auth.has_credentials(ctx)
                or not auth._credential(ctx.auth.get("access_token"))):
            return
        packet = _payload(payload)
        message = json.dumps({"payload": packet, "access_token": ctx.auth["access_token"],
                              "backend_origin": ctx.auth["backend_origin"],
                              "account_id": ctx.auth.get("account", {}).get("uuid")}).encode("utf-8")
        if len(message) > 4096:
            return
        _workers[:] = [worker for worker in _workers if worker.poll() is None]
        if len(_workers) >= 4:
            return
        options = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL,
                   "stderr": subprocess.DEVNULL, "close_fds": True,
                   "env": {**os.environ, "LEARNOVA_HOME": str(store.CONFIG_PATH.parent)}}
        if os.name == "nt":
            options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                   "--send-install"], **options)
        _workers.append(worker)
        try:
            worker.stdin.write(message)
        finally:
            worker.stdin.close()
    except Exception:
        return


def _send(event: str, payload: dict, ctx=None) -> None:
    try:
        _do_send(event, payload, ctx)
    except Exception:
        pass


def _do_send(event: str, payload: dict, ctx=None) -> None:
    if event != "install" or not store.telemetry_enabled():
        return
    config = store.load_config()
    endpoint = config.get("telemetry_endpoint")
    if not endpoint:
        return
    current = CliContext(config=config, auth=store.load_auth())
    if (ctx is None or not auth.has_credentials(current)
            or not auth._credential(ctx.auth.get("access_token"))
            or current.auth.get("backend_origin") != ctx.auth.get("backend_origin")
            or current.auth.get("account", {}).get("uuid") != ctx.auth.get("account", {}).get("uuid")):
        return
    current.auth = {**current.auth, "access_token": ctx.auth["access_token"]}
    packet = _payload(payload)
    cached, _ = store.load_index_cache(config.get("mirror_repo_url"))
    if cached is None:
        return
    index = validate_index(cached)
    matches = [skill for skill in index['skills'] if skill['dir'] == packet['skill']]
    if len(matches) != 1:
        return
    identity = matches[0].get('community_id')
    if (not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', identity)
            or sum(skill.get('community_id') == identity for skill in index['skills']) != 1
            or sum(v['version'] == packet['version'] for v in matches[0]['versions']) != 1):
        return
    path = endpoint + '?' + urlencode({'community_id': identity})
    auth.request(current, "POST", path, body={"event": "install", **packet}, timeout=2,
                 allow_refresh=False)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--send-install":
        try:
            incoming = sys.stdin.buffer.read(4097)
            if len(incoming) > 4096:
                raise ValueError("event too large")
            message = json.loads(incoming)
            config, credentials = store.load_config(), store.load_auth()
            if (message.get("backend_origin") == credentials.get("backend_origin")
                    and message.get("account_id") == credentials.get("account", {}).get("uuid")):
                context = CliContext(config=config, auth={**credentials, "access_token": message["access_token"]})
                _send("install", message["payload"], context)
        except Exception:
            pass
