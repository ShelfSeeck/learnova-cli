"""BackendSource：通过 Learnova 后端 API 提供数据（在线、实时）。"""

from pathlib import Path

from core.errors import CliError


class BackendSource:
    def __init__(self, ctx):
        self.ctx = ctx

    def search(self, keyword: str, tags: list[str], sort: str) -> list[dict]:
        raise CliError("NOT_IMPLEMENTED", "BackendSource.search 尚未实现")

    def info(self, skill_dir: str) -> dict:
        raise CliError("NOT_IMPLEMENTED", "BackendSource.info 尚未实现")

    def fetch(self, skill_dir: str, version: str | None, dest: Path) -> Path:
        raise CliError("NOT_IMPLEMENTED", "BackendSource.fetch 尚未实现")
