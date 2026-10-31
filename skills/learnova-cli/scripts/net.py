"""HTTP 封装：统一超时、UA，错误转换为 CliError。命令层不直接碰 urllib。"""

import io
import json
import socket
import time
import http.client
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.errors import AuthError, CliError

DEFAULT_TIMEOUT = 10
DOWNLOAD_TIMEOUT = 60
USER_AGENT = "learnova-cli/0.1"
MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port or (443 if parts.scheme.lower() == "https" else 80)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and req.has_header("Authorization") and _origin(req.full_url) != _origin(newurl):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "cross-origin redirect blocked for authenticated request",
                headers,
                fp,
            )
        return redirected


_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def _build(url: str, method: str = "GET", token: str | None = None) -> urllib.request.Request:
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def _open(req: urllib.request.Request, timeout: float, allow_404: bool = False):
    try:
        return _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        e.close()
        if allow_404 and e.code == 404:
            return None
        if e.code == 401:
            raise AuthError("UNAUTHORIZED", "凭证失效")
        raise CliError("HTTP_ERROR", f"HTTP 请求失败 ({e.code})")
    except (TimeoutError, socket.timeout) as exc:
        raise CliError("HTTP_TIMEOUT", "HTTP 请求超时") from exc
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            raise CliError("HTTP_TIMEOUT", "HTTP 请求超时") from e
        raise CliError("UNREACHABLE", "服务不可达")
    except (OSError, http.client.HTTPException):
        raise CliError("HTTP_ERROR", "HTTP 连接中断") from None


def _copy_limited(resp, out, max_bytes: int, label: str, deadline=None) -> None:
    length = resp.headers.get("Content-Length")
    if length:
        try:
            if int(length) > max_bytes:
                raise CliError("HTTP_RESPONSE_TOO_LARGE", f"{label}超过 {max_bytes} 字节安全上限")
        except ValueError:
            pass
    total = 0
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise CliError("HTTP_TIMEOUT", "HTTP 响应超时")
        try:
            if deadline is not None:
                remaining = max(0.001, deadline - time.monotonic())
                sock = getattr(getattr(getattr(resp, 'fp', None), 'raw', None), '_sock', None)
                if sock is not None:
                    sock.settimeout(remaining)
            read = getattr(resp, 'read1', resp.read)
            chunk = read(64 * 1024)
        except (TimeoutError, socket.timeout) as exc:
            raise CliError("HTTP_TIMEOUT", "HTTP 响应超时") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise CliError("HTTP_ERROR", "HTTP 响应中断") from exc
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise CliError("HTTP_RESPONSE_TOO_LARGE", f"{label}超过 {max_bytes} 字节安全上限")
        out.write(chunk)
    if length and length.isdigit() and total != int(length):
        raise CliError("HTTP_ERROR", "HTTP 响应中断")


def _read_limited(resp, max_bytes: int, deadline=None) -> bytes:
    out = io.BytesIO()
    _copy_limited(resp, out, max_bytes, "响应", deadline)
    return out.getvalue()


def request(method: str, url: str, body: dict | None = None,
            token: str | None = None, timeout: float = DEFAULT_TIMEOUT,
            max_bytes: int = MAX_JSON_BYTES) -> dict:
    deadline = time.monotonic() + timeout
    req = _build(url, method, token)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with _open(req, timeout) as resp:
            return json.loads(_read_limited(resp, max_bytes, deadline))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CliError("HTTP_ERROR", "服务返回了非法 JSON") from exc


def get_text(url: str, timeout: float = DEFAULT_TIMEOUT, max_bytes: int = MAX_TEXT_BYTES) -> str | None:
    """GET 文本内容；404 返回 None（可选文件如 README）。"""
    deadline = time.monotonic() + timeout
    resp = _open(_build(url), timeout, allow_404=True)
    if resp is None:
        return None
    with resp:
        return _read_limited(resp, max_bytes, deadline).decode("utf-8", errors="replace")


def download(url: str, dest: Path, timeout: float = DOWNLOAD_TIMEOUT,
             max_bytes: int = MAX_DOWNLOAD_BYTES) -> None:
    """下载二进制内容到 dest（如 GitLab archive）。"""
    deadline = time.monotonic() + timeout
    with _open(_build(url), timeout) as resp, open(dest, "wb") as f:
        _copy_limited(resp, f, max_bytes, "下载", deadline)


def request_bytes(method: str, url: str, body: bytes | None = None,
                  token: str | None = None, timeout: float = DOWNLOAD_TIMEOUT,
                  max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """Raw binary request/response; callers explicitly choose auth and byte limits."""
    deadline = time.monotonic() + timeout
    req = _build(url, method, token)
    req.add_header("Accept", "application/octet-stream")
    if body is not None:
        req.data = body
        req.add_header("Content-Type", "application/octet-stream")
    with _open(req, timeout) as resp:
        return _read_limited(resp, max_bytes, deadline)
