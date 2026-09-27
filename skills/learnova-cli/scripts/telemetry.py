"""遥测：fire-and-forget。失败静默，永不阻塞主流程。

只在 store.telemetry_enabled() 为真时上报（默认跟随登录态）。
"""

import threading

import store


def report(event: str, payload: dict) -> None:
    """异步上报事件，任何异常静默吞掉。"""
    try:
        enabled = store.telemetry_enabled()
    except Exception:
        return
    if not enabled:
        return
    thread = threading.Thread(target=_send, args=(event, payload), daemon=True)
    thread.start()


def _send(event: str, payload: dict) -> None:
    try:
        _do_send(event, payload)
    except Exception:
        pass


def _do_send(event: str, payload: dict) -> None:
    raise NotImplementedError("遥测端点待后端提供后实现")
