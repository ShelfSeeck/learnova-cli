"""命令注册表。新命令 = 新模块 + 此处一行。"""

from . import (
    build,
    config,
    info,
    init,
    install,
    list_cmd,
    login,
    logout,
    publish,
    search,
    self_update,
    status,
    uninstall,
    upgrade,
    whoami,
)

MODULES = [
    build,
    init,
    search,
    info,
    install,
    list_cmd,
    uninstall,
    upgrade,
    self_update,
    login,
    logout,
    whoami,
    publish,
    status,
    config,
]
