"""SkillSource 协议（端口）：一个技能数据源必须会的三件事。

命令层只面向此协议编程，不关心背后是后端 API 还是 git 镜像。
"""

from pathlib import Path
from typing import Protocol


class SkillSource(Protocol):
    def search(self, keyword: str, tags: list[str], sort: str) -> list[dict]:
        """检索技能摘要列表。"""
        ...

    def info(self, skill_dir: str) -> dict:
        """技能详情：元数据 + 版本列表 + README。"""
        ...

    def fetch(self, skill_dir: str, version: str | None, dest: Path) -> Path:
        """把指定版本（默认最新）的技能文件拉到 dest，返回技能目录路径。"""
        ...
