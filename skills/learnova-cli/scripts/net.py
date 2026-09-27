"""HTTP 封装：统一超时、UA，错误转换为 CliError。命令层不直接碰 urllib。"""

import io
import json
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
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


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
        if allow_404 and e.code == 404:
            return None
        if e.code == 401:
            raise AuthError("UNAUTHORIZED", f"凭证失效: {req.get_method()} {req.full_url}")
        raise CliError("HTTP_ERROR", f"{req.get_method()} {req.full_url} -> {e.code}")
    except urllib.error.URLError as e:
        raise CliError("UNREACHABLE", f"服务不可达: {req.full_url} ({e.reason})")


def _copy_limited(resp, out, max_bytes: int, label: str) -> None:
    length = resp.headers.get("Content-Length")
    if length:
        try:
            if int(length) > max_bytes:
                raise CliError("HTTP_RESPONSE_TOO_LARGE", f"{label}超过 {max_bytes} 字节安全上限")
        except ValueError:
            pass
    total = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise CliError("HTTP_RESPONSE_TOO_LARGE", f"{label}超过 {max_bytes} 字节安全上限")
        out.write(chunk)


def _read_limited(resp, max_bytes: int) -> bytes:
    out = io.BytesIO()
    _copy_limited(resp, out, max_bytes, "响应")
    return out.getvalue()


def request(method: str, url: str, body: dict | None = None,
            token: str | None = None, timeout: float = DEFAULT_TIMEOUT,
            max_bytes: int = MAX_JSON_BYTES) -> dict:
    req = _build(url, method, token)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with _open(req, timeout) as resp:
            return json.loads(_read_limited(resp, max_bytes))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CliError("HTTP_ERROR", f"服务返回了非法 JSON: {url}") from exc


def get_text(url: str, timeout: float = DEFAULT_TIMEOUT, max_bytes: int = MAX_TEXT_BYTES) -> str | None:
    """GET 文本内容；404 返回 None（可选文件如 README）。"""
    resp = _open(_build(url), timeout, allow_404=True)
    if resp is None:
        return None
    with resp:
        return _read_limited(resp, max_bytes).decode("utf-8", errors="replace")


def download(url: str, dest: Path, timeout: float = DOWNLOAD_TIMEOUT,
             max_bytes: int = MAX_DOWNLOAD_BYTES) -> None:
    """下载二进制内容到 dest（如 GitLab archive）。"""
    with _open(_build(url), timeout) as resp, open(dest, "wb") as f:
        _copy_limited(resp, f, max_bytes, "下载")
