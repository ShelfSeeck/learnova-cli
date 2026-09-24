"""Resolver：数据源选择的唯一决策者。

默认 Backend 优先、请求失败降级 Mirror；--offline 强制 Mirror。
改回退策略只动这里；新源在此注册。
"""

from core.errors import CliError
from sources.backend import BackendSource
from sources.mirror import MirrorSource


def resolve(ctx):
    if ctx.offline:
        return MirrorSource(ctx)
    raise CliError("NOT_IMPLEMENTED", "resolver 尚未实现（后端可达性探测 + 降级）")
