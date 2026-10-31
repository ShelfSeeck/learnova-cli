"""唯一数据源决策点：认证后端优先，可恢复故障降级镜像。"""

import tempfile
from pathlib import Path

import auth
from core.errors import CliError
from core.skill import validate_skill_dir
from sources.backend import BackendSource
from sources.mirror import MirrorSource


def _recoverable(error):
    if error.code in {"UNREACHABLE", "HTTP_TIMEOUT", "UNAUTHORIZED"}:
        return True
    if error.code == "HTTP_ERROR":
        status = getattr(error, "http_status", None)
        return status is None or status in {401, 403} or 500 <= status < 600
    return False


class FallbackSource:
    def __init__(self, ctx):
        self.backend = BackendSource(ctx)
        self.mirror = MirrorSource(ctx)
        self.active = self.backend

    @property
    def name(self):
        return self.active.name

    def _call(self, operation, *args):
        try:
            return operation(self.active, *args)
        except CliError as exc:
            if self.active is self.mirror or not _recoverable(exc):
                raise
        self.active = self.mirror
        return operation(self.mirror, *args)

    def search(self, keyword, tags, sort):
        return self._call(lambda source, *args: source.search(*args), keyword, tags, sort)

    def info(self, skill_dir):
        return self._call(lambda source, *args: source.info(*args), skill_dir)

    def fetch(self, skill_dir, version, dest):
        validate_skill_dir(skill_dir)
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / skill_dir
        if target.exists() or target.is_symlink():
            raise CliError("SKILL_PACKAGE_INVALID", "技能候选目标已存在")

        def fetch_candidate(source):
            # 每次尝试用独立目录，后端中途失败不污染镜像候选。
            with tempfile.TemporaryDirectory(prefix="learnova-source-", dir=dest) as tmp:
                root = Path(tmp)
                candidate = Path(source.fetch(skill_dir, version, root))
                if (candidate.is_symlink() or not candidate.is_dir()
                        or not candidate.resolve().is_relative_to(root.resolve()) or candidate == root):
                    raise CliError("SKILL_PACKAGE_INVALID", "数据源返回了非法候选目录")
                candidate.replace(target)
            return target

        return self._call(fetch_candidate)


def resolve(ctx):
    if (ctx.offline or not (ctx.config.get("backend_url") or "").strip()
            or not auth.has_credentials(ctx)):
        return MirrorSource(ctx)
    return FallbackSource(ctx)
