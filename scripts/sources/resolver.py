"""Resolver：数据源选择的唯一决策者。

--offline 或未配置后端时返回 MirrorSource；否则 BackendSource（M3 补降级链）。
改回退策略只动这里；新源在此注册。
"""

from sources.backend import BackendSource
from sources.mirror import MirrorSource


def resolve(ctx):
    if ctx.offline or not (ctx.config.get("backend_url") or "").strip():
        return MirrorSource(ctx)
    return BackendSource(ctx)
