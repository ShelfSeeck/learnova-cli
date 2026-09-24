"""MirrorSource：通过 git 镜像提供数据（离线兜底）。

search/info 读本地 index.json 缓存；fetch 按 versions[].commit
调 GitLab archive API 下载解压。
"""

from pathlib import Path

from core.errors import CliError


class MirrorSource:
    def __init__(self, ctx):
        self.ctx = ctx

    def search(self, keyword: str, tags: list[str], sort: str) -> list[dict]:
        raise CliError("NOT_IMPLEMENTED", "MirrorSource.search 尚未实现")

    def info(self, skill_dir: str) -> dict:
        raise CliError("NOT_IMPLEMENTED", "MirrorSource.info 尚未实现")

    def fetch(self, skill_dir: str, version: str | None, dest: Path) -> Path:
        raise CliError("NOT_IMPLEMENTED", "MirrorSource.fetch 尚未实现")
