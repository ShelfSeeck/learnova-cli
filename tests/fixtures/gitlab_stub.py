"""本地 GitLab 镜像 stub：T1 专用，只模拟 CLI 实际用到的两个端点。

- GET .../raw/main/<path>                     → fixture 工作区文件（如 index.json、README.md）
- GET .../repository/archive.tar.gz?sha=<sha>  → 动态打包工作区或 .commits/<sha>/ 历史快照

不模拟完整 GitLab API；记录收到的请求路径供断言。
"""

import io
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

RAW_MARKER = "/raw/main/"
ARCHIVE_MARKER = "/repository/archive.tar.gz"
COMMITS_DIR = ".commits"


class MirrorStub:
    def __init__(self, worktree: Path):
        self.worktree = Path(worktree)
        self.requests: list[str] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def serve_raw(self, relpath: str) -> bytes | None:
        rel = unquote(relpath).lstrip("/")
        if not rel or ".." in Path(rel).parts:
            return None
        target = self.worktree / rel
        if not target.is_file():
            return None
        return target.read_bytes()

    def archive_for(self, sha: str) -> bytes:
        """打包工作区（跳过点开头目录）或 .commits/<sha>/ 历史快照。"""
        snapshot = self.worktree / COMMITS_DIR / sha
        root = snapshot if snapshot.is_dir() else self.worktree
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for item in sorted(root.iterdir()):
                if item.name.startswith("."):
                    continue
                tar.add(item, arcname=f"mirror-{sha}/{item.name}", recursive=True)
        return buf.getvalue()


def _make_handler(stub: MirrorStub):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            stub.requests.append(self.path)
            if RAW_MARKER in parsed.path:
                data = stub.serve_raw(parsed.path.split(RAW_MARKER, 1)[1])
                if data is None:
                    self.send_error(404)
                    return
                self._send(200, data, "text/plain; charset=utf-8")
                return
            if parsed.path.endswith(ARCHIVE_MARKER):
                sha = parse_qs(parsed.query).get("sha", [""])[0]
                if not sha:
                    self.send_error(400)
                    return
                self._send(200, stub.archive_for(sha), "application/gzip")
                return
            self.send_error(404)

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler
