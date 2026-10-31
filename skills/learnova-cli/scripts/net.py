"""HTTP 封装：统一超时、UA，错误转换为 CliError。命令层不直接碰 urllib。"""

import io
import json
import re
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
        authenticated = req.has_header("Authorization") or req.has_header("Cookie")
        if redirected is not None and authenticated and _origin(req.full_url) != _origin(newurl):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "cross-origin redirect blocked for authenticated request",
                headers,
                fp,
            )
        return redirected


_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def _build(url: str, method: str = "GET", token: str | None = None,
           headers: dict | None = None) -> urllib.request.Request:
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    if token:
        headers = {**(headers or {}), "Authorization": f"Bearer {token}"}
    _add_headers(req, headers)
    return req


def _add_headers(req, headers) -> None:
    for key, value in (headers or {}).items():
        if (not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                or key.lower() in {"host", "content-length", "transfer-encoding", "connection",
                                   "proxy-authorization", "proxy-connection", "keep-alive", "te", "trailer", "upgrade"}
                or not isinstance(value, str) or any(ord(char) < 32 or ord(char) > 126 for char in value)):
            raise CliError("BAD_USAGE", "HTTP 请求头无效", exit_code=2)
        req.add_header(key, value)


def _open(req: urllib.request.Request, timeout: float, allow_404: bool = False):
    try:
        return _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        e.close()
        if allow_404 and e.code == 404:
            return None
        if e.code == 401:
            error = AuthError("UNAUTHORIZED", "凭证失效")
        else:
            error = CliError("HTTP_ERROR", f"HTTP 请求失败 ({e.code})")
        error.http_status = e.code
        raise error from None
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
            max_bytes: int = MAX_JSON_BYTES, *, headers: dict | None = None,
            response_headers: dict | None = None, data: bytes | None = None,
            content_type: str | None = None):
    """JSON response; optional response headers use lowercase keys and lists of values."""
    if body is not None and data is not None:
        raise CliError("BAD_USAGE", "JSON 与原始请求内容不能同时指定", exit_code=2)
    deadline = time.monotonic() + timeout
    req = _build(url, method, token, headers)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    elif data is not None:
        req.data = data
        req.add_header("Content-Type", content_type or "application/octet-stream")
    if content_type is not None:
        _add_headers(req, {"Content-Type": content_type})
    try:
        with _open(req, timeout) as resp:
            _capture_headers(resp, response_headers)
            raw = _read_limited(resp, max_bytes, deadline)
            if not raw and resp.status == 204:
                return None
            return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CliError("HTTP_ERROR", "服务返回了非法 JSON") from exc


def _capture_headers(resp, target: dict | None) -> None:
    if target is not None:
        target.clear()
        target.update({key.lower(): resp.headers.get_all(key) for key in resp.headers})


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
                  max_bytes: int = MAX_DOWNLOAD_BYTES, *, headers: dict | None = None,
                  response_headers: dict | None = None,
                  content_type: str | None = None) -> bytes:
    """Raw binary request/response; callers explicitly choose auth and byte limits."""
    deadline = time.monotonic() + timeout
    req = _build(url, method, token, headers)
    req.add_header("Accept", "application/octet-stream")
    if body is not None:
        req.data = body
        # An empty value preserves the upload signature and prevents urllib's form default.
        _add_headers(req, {"Content-Type": content_type if content_type is not None else ""})
    elif content_type is not None:
        _add_headers(req, {"Content-Type": content_type})
    with _open(req, timeout) as resp:
        _capture_headers(resp, response_headers)
        return _read_limited(resp, max_bytes, deadline)
