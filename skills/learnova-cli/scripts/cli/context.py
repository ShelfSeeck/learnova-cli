"""CliContext：handler 的注入接缝。

集中存放配置、凭证、运行标志。测试可构造指向临时目录的假 ctx。
"""

from dataclasses import dataclass, field
from pathlib import Path

import store


@dataclass
class CliContext:
    config: dict = field(default_factory=dict)
    auth: dict = field(default_factory=dict)
    as_json: bool = False
    offline: bool = False
    cwd: Path = field(default_factory=Path.cwd)

    def install_project_scope(self, flag: bool) -> bool:
        """install 的 --project 显式优先；否则使用 install_scope 默认值。"""
        return bool(flag) or self.config.get("install_scope") == "project"

    def selected_agents(self, explicit: list[str] | None = None) -> list[str] | None:
        """显式 --agent 优先，否则应用 config.agents；None 表示自动检测。"""
        configured = explicit if explicit is not None else self.config.get("agents", "auto")
        return None if configured == "auto" else list(configured)

    @classmethod
    def build(cls, args) -> "CliContext":
        return cls(
            config=store.load_config(),
            auth=store.load_auth(),
            as_json=getattr(args, "json", False),
            offline=getattr(args, "offline", False),
        )
