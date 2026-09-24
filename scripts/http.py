"""HTTP 封装：统一超时、UA，错误转换为 CliError。命令层不直接碰 urllib。"""

import json
import urllib.error
import urllib.request

from core.errors import AuthError, CliError

DEFAULT_TIMEOUT = 10
USER_AGENT = "learnova-cli/0.1"


def request(method: str, url: str, body: dict | None = None,
            token: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise AuthError("UNAUTHORIZED", f"凭证失效: {method} {url}")
        raise CliError("HTTP_ERROR", f"{method} {url} -> {e.code}")
    except urllib.error.URLError as e:
        raise CliError("UNREACHABLE", f"服务不可达: {url} ({e.reason})")
