"""账号服务：终端登录、绑定来源的刷新凭证及认证 HTTP 请求。"""

import getpass
from http.cookies import CookieError, SimpleCookie
import json
import sys
import time
import urllib.parse
import uuid
import warnings

from core.errors import AuthError, CliError
import net
import store

REFRESH_COOKIE = "refresh_token"


def backend_url(ctx) -> str:
    """Validate the configured API root without including it in errors."""
    value = ctx.config.get("backend_url", "")
    if not value:
        raise CliError("BACKEND_NOT_CONFIGURED", "请先通过 config set backend_url 配置后端地址")
    return validate_base_url(value)


def validate_base_url(value) -> str:
    """Validate an HTTP API/repository root before using or displaying configuration."""
    try:
        parts = urllib.parse.urlsplit(value)
        valid = (isinstance(value, str) and parts.scheme.lower() in {"http", "https"}
                 and parts.hostname and parts.port != 0 and parts.username is None
                 and parts.password is None and "?" not in value and "#" not in value
                 and not any(char.isspace() or ord(char) < 32 for char in value)
                 and "\\" not in value)
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise CliError("BAD_USAGE", "服务地址必须是不含凭证、查询参数或片段的 HTTP(S) 地址", exit_code=2)
    return value.rstrip("/")


def _origin(base: str) -> str:
    scheme, host, port = net._origin(base)
    host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host}:{port}"


def _credential(value) -> bool:
    return isinstance(value, str) and bool(value) and all(32 < ord(char) < 127 for char in value) and not any(char in value for char in ';,"\\')


def has_credentials(ctx) -> bool:
    """Credentials from another backend are treated as logged out."""
    try:
        origin = _origin(backend_url(ctx))
    except CliError:
        return False
    return (isinstance(ctx.auth, dict) and ctx.auth.get("backend_origin") == origin
            and _credential(ctx.auth.get("refresh_token")))


def _save(ctx) -> None:
    # Access tokens remain in this process; only the rotating refresh credential is persisted.
    store.save_auth({key: value for key, value in ctx.auth.items()
                     if key in {"backend_origin", "refresh_token", "username", "account"}})


def _clear(ctx) -> None:
    ctx.auth = {}
    store.clear_auth()


def _invalid_response():
    raise CliError("BACKEND_RESPONSE_INVALID", "后端返回的账号数据无效")


def _tokens(ctx, response, response_headers, *, origin: str) -> None:
    if not isinstance(response, dict) or not _credential(response.get("access_token")):
        _invalid_response()
    refresh = None
    values = response_headers.get("set-cookie", [])
    if isinstance(values, str):
        values = [values]
    for value in values:
        cookie = SimpleCookie()
        try:
            cookie.load(value)
        except CookieError:
            _invalid_response()
        if REFRESH_COOKIE in cookie:
            morsel = cookie[REFRESH_COOKIE]
            if morsel["domain"] or not _credential(morsel.value):
                _invalid_response()
            refresh = morsel.value
    if refresh is None:
        _invalid_response()
    ctx.auth = {**ctx.auth, "backend_origin": origin,
                "refresh_token": refresh, "access_token": response["access_token"]}
    _save(ctx)


def _refresh(ctx, *, timeout=net.DEFAULT_TIMEOUT) -> None:
    if not has_credentials(ctx):
        raise AuthError("UNAUTHORIZED", "请在终端亲自运行 learnova-cli login 登录")
    base = backend_url(ctx)
    response_headers = {}
    try:
        response = net.request("POST", base + "/auth/refresh", timeout=timeout,
                               headers={"Cookie": f"{REFRESH_COOKIE}={ctx.auth['refresh_token']}"},
                               response_headers=response_headers)
    except AuthError as exc:
        _clear(ctx)
        error = AuthError("UNAUTHORIZED", "登录已失效，请在终端亲自运行 learnova-cli login")
        error.http_status = getattr(exc, "http_status", 401)
        raise error from None
    _tokens(ctx, response, response_headers, origin=_origin(base))


def _request_url(ctx, path: str) -> str:
    base = backend_url(ctx)
    try:
        parts = urllib.parse.urlsplit(path)
        valid = (path.startswith("/") and not path.startswith("//") and not parts.scheme
                 and not parts.netloc and "#" not in path and "\\" not in path
                 and not any(ord(char) < 32 or char.isspace() for char in path)
                 and all(segment not in {".", ".."} for segment in urllib.parse.unquote(parts.path).split("/")))
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise CliError("BAD_USAGE", "后端请求路径无效", exit_code=2)
    return base + path


def request(ctx, method: str, path: str, body=None, *, data=None, content_type=None,
            timeout=net.DEFAULT_TIMEOUT, max_bytes=net.MAX_JSON_BYTES, headers=None,
            binary=False, allow_refresh=True):
    """Authenticated JSON/bytes request; refresh once after an expired access token."""
    url = _request_url(ctx, path)
    if body is not None and data is not None:
        raise CliError("BAD_USAGE", "JSON 与原始请求内容不能同时指定", exit_code=2)
    if not has_credentials(ctx):
        raise AuthError("UNAUTHORIZED", "请在终端亲自运行 learnova-cli login 登录")
    extra = dict(headers or {})
    if any(not isinstance(key, str) or key.lower() in {"authorization", "cookie"} for key in extra):
        raise CliError("BAD_USAGE", "认证请求头由账号服务管理", exit_code=2)
    if not _credential(ctx.auth.get("access_token")):
        if not allow_refresh:
            raise AuthError("UNAUTHORIZED", "当前请求没有可用访问凭证")
        _refresh(ctx, timeout=timeout)

    def send():
        outgoing = dict(extra)
        if method.upper() not in {"GET", "HEAD", "OPTIONS"} and not path.split("?", 1)[0].startswith("/auth/"):
            outgoing["X-Nonce"] = str(uuid.uuid4())
            outgoing["X-Nonce-Timestamp"] = str(time.time())
        kwargs = {"token": ctx.auth["access_token"], "timeout": timeout, "max_bytes": max_bytes}
        if outgoing:
            kwargs["headers"] = outgoing
        if data is not None:
            kwargs["data"] = data
        if content_type is not None:
            kwargs["content_type"] = content_type
        if binary:
            kwargs.pop("data", None)
            payload = data
            if body is not None:
                payload = json.dumps(body).encode()
                kwargs.setdefault("content_type", "application/json")
            return net.request_bytes(method, url, body=payload, **kwargs)
        return net.request(method, url, body=body, **kwargs)

    try:
        return send()
    except AuthError:
        if not allow_refresh:
            raise
        _refresh(ctx, timeout=timeout)
        try:
            return send()
        except AuthError as exc:
            _clear(ctx)
            error = AuthError("UNAUTHORIZED", "登录已失效，请在终端亲自运行 learnova-cli login")
            error.http_status = getattr(exc, "http_status", 401)
            raise error from None


def _account(response) -> dict:
    if not isinstance(response, dict) or not isinstance(response.get("username"), str) or not response["username"]:
        _invalid_response()
    return {key: response[key] for key in ("username", "uuid", "email", "role")
            if isinstance(response.get(key), str)}


def current_user(ctx) -> dict:
    account = _account(request(ctx, "GET", "/auth/me"))
    ctx.auth.update(account=account, username=account["username"])
    _save(ctx)
    return account


def login(ctx, email: str, password: str) -> dict:
    base = backend_url(ctx)
    response_headers = {}
    response = net.request("POST", base + "/auth/login", body={"email": email, "password": password},
                           response_headers=response_headers)
    ctx.auth = {}
    _tokens(ctx, response, response_headers, origin=_origin(base))
    return current_user(ctx)


def terminal_available() -> bool:
    return bool(sys.stdin.isatty() and sys.stderr.isatty())


def login_interactive(ctx) -> dict:
    if not terminal_available():
        raise CliError("BAD_USAGE", "登录需要本人在终端运行 learnova-cli login；不要通过参数或环境变量传入密码", exit_code=2)
    backend_url(ctx)
    try:
        print("邮箱: ", end="", file=sys.stderr, flush=True)
        email = input().strip()
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("密码: ")
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        raise CliError("BAD_USAGE", "登录已取消；请在终端重新运行 learnova-cli login", exit_code=2) from None
    if not email or not password:
        raise CliError("BAD_USAGE", "邮箱和密码不能为空", exit_code=2)
    return {"ok": True, "logged_in": True, **login(ctx, email, password)}


def logout(ctx) -> dict:
    try:
        if has_credentials(ctx):
            net.request("POST", backend_url(ctx) + "/auth/logout", timeout=3,
                        headers={"Cookie": f"{REFRESH_COOKIE}={ctx.auth['refresh_token']}"})
    except CliError:
        pass
    finally:
        _clear(ctx)
    return {"ok": True, "message": "已退出登录"}


def whoami(ctx) -> dict:
    if not has_credentials(ctx):
        return {"logged_in": False, "username": None}
    return {"logged_in": True, **current_user(ctx)}
