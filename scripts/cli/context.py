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

    @classmethod
    def build(cls, args) -> "CliContext":
        return cls(
            config=store.load_config(),
            auth=store.load_auth(),
            as_json=getattr(args, "json", False),
            offline=getattr(args, "offline", False),
        )
