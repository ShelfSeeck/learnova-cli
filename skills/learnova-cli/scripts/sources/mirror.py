"""MirrorSource：通过 git 镜像仓提供数据（后端不可达时的兜底）。

索引：优先读本地 index_cache.json，缺失或超过 TTL 时从镜像仓 raw 端点拉取；
拉取失败退回过期缓存并标记陈旧。
内容：按 versions[].commit 调 GitLab archive API 下载解压。
"""

import shutil
import tarfile
import tempfile
import urllib.parse
from pathlib import Path, PurePosixPath

import net
import store
from core import index as core_index
from core.errors import CliError
from core.skill import parse_skill_meta, validate_skill_dir, write_install_metadata

BRANCH = "main"
MAX_ARCHIVE_MEMBERS = 2000
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 25 * 1024 * 1024


class MirrorSource:
    name = "mirror"

    def __init__(self, ctx):
        self.ctx = ctx

    def search(self, keyword: str, tags: list[str], sort: str) -> dict:
        index, stale = self._index()
        skills = core_index.sort_skills(core_index.filter_skills(index, keyword, tags), sort)
        return {
            "skills": [core_index.summarize(s) for s in skills],
            "total": len(skills),
            **self._meta(index, stale),
        }

    def info(self, skill_dir: str) -> dict:
        validate_skill_dir(skill_dir)
        index, stale = self._index()
        skill = self._require_skill(index, skill_dir)
        readme = net.get_text(self._raw_url(f"{skill_dir}/README.md")) or ""
        return {
            **core_index.summarize(skill),
            "versions": skill.get("versions", []),
            "readme": readme,
            **self._meta(index, stale),
        }

    def fetch(self, skill_dir: str, version: str | None, dest: Path) -> Path:
        validate_skill_dir(skill_dir)
        index, _ = self._index()
        skill = self._require_skill(index, skill_dir)
        entry = self._version_entry(skill, version)
        # 必须带 path= 让服务端只打包技能子目录：镜像仓整体已超 50MB 下载上限，
        # 全仓 archive 会让所有安装失败（HTTP_RESPONSE_TOO_LARGE）。
        url = (
            f"{self._api_base()}/repository/archive.tar.gz"
            f"?sha={entry['commit']}&path={urllib.parse.quote(skill_dir, safe='')}"
        )
        with tempfile.TemporaryDirectory(prefix="learnova-archive-") as tmp:
            tarball = Path(tmp) / "archive.tar.gz"
            net.download(url, tarball)
            extracted = _extract_skill(tarball, skill_dir, dest)
            _validate_skill_package(extracted, entry["version"])
            write_install_metadata(extracted, skill_dir, entry["version"])
            return extracted

    def _require_skill(self, index: dict, skill_dir: str) -> dict:
        skill = core_index.find_skill(index, skill_dir)
        if skill is None:
            raise CliError("SKILL_NOT_FOUND", f"索引中不存在技能: {skill_dir}")
        return skill

    def _version_entry(self, skill: dict, version: str | None) -> dict:
        versions = skill.get("versions", [])
        if version is None:
            return max(versions, key=lambda v: v.get("created_at", 0))
        entry = next((item for item in versions if item.get("version") == version), None)
        if entry is not None:
            return entry
        raise CliError("VERSION_NOT_FOUND", f"{skill.get('dir')} 不存在版本 {version}")

    def _repo_url(self) -> str:
        url = (self.ctx.config.get("mirror_repo_url") or "").strip().rstrip("/")
        if not url:
            raise CliError("MIRROR_NOT_CONFIGURED", "未配置镜像仓地址，执行 config set mirror_repo_url <url>")
        return url

    def _raw_url(self, relpath: str) -> str:
        return f"{self._repo_url()}/-/raw/{BRANCH}/{relpath}"

    def _api_base(self) -> str:
        """仓库网页地址 → GitLab API v4 project base。"""
        parts = urllib.parse.urlsplit(self._repo_url())
        path = parts.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        encoded = urllib.parse.quote(path, safe="")
        return f"{parts.scheme}://{parts.netloc}/api/v4/projects/{encoded}".rstrip("/")

    def _index(self) -> tuple[dict, bool]:
        """返回 (index, stale)。"""
        cached, stale = store.load_index_cache()
        if self.ctx.offline:
            if cached is None:
                raise CliError("OFFLINE_CACHE_MISS", "严格离线模式下没有本地索引缓存")
            return core_index.validate_index(cached), stale
        if cached is not None and not stale:
            return core_index.validate_index(cached), False
        try:
            fetched = net.request("GET", self._raw_url("index.json"))
        except CliError:
            if cached is not None:
                return core_index.validate_index(cached), True
            raise
        core_index.validate_index(fetched)
        store.save_index_cache(fetched)
        return fetched, False

    def _meta(self, index: dict, stale: bool) -> dict:
        return {
            "source": self.name,
            "generated_at": index.get("generated_at"),
            "stale": stale,
        }


def _extract_skill(tarball: Path, skill_dir: str, dest: Path) -> Path:
    """从 GitLab archive 中取出 <顶层>/<skill_dir>/ 到 dest/<skill_dir>。

    不信任归档路径：只提取前缀内成员，拒绝链接、越界路径与超限内容。
    """
    validate_skill_dir(skill_dir)
    target_root = dest / skill_dir
    target_root.mkdir(parents=True, exist_ok=True)
    root_resolved = target_root.resolve()
    try:
        tar = tarfile.open(tarball, "r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise CliError("MIRROR_ARCHIVE_INVALID", "无法读取镜像 archive") from exc
    with tar:
        top = None
        files = total_bytes = member_count = 0
        for member in tar:
            member_count += 1
            if member_count > MAX_ARCHIVE_MEMBERS:
                raise CliError("MIRROR_ARCHIVE_INVALID", "archive 文件项数量超过安全上限")
            parts = _safe_tar_parts(member.name)
            if not parts:
                continue
            if top is None:
                top = parts[0]
            elif parts[0] != top:
                raise CliError("MIRROR_ARCHIVE_INVALID", "archive 存在多个顶层目录")
            if len(parts) < 3 or parts[1] != skill_dir:
                continue
            if member.issym() or member.islnk():
                raise CliError("MIRROR_ARCHIVE_INVALID", f"archive 中禁止链接成员: {member.name}")
            if not member.isdir() and not member.isfile():
                raise CliError("MIRROR_ARCHIVE_INVALID", f"archive 中存在不支持的成员: {member.name}")
            if member.size > MAX_MEMBER_BYTES:
                raise CliError("MIRROR_ARCHIVE_INVALID", f"archive 单文件超过安全上限: {member.name}")
            total_bytes += member.size
            if total_bytes > MAX_EXTRACTED_BYTES:
                raise CliError("MIRROR_ARCHIVE_INVALID", "archive 解压后总大小超过安全上限")
            target = target_root.joinpath(*parts[2:])
            if not target.resolve(strict=False).is_relative_to(root_resolved):
                raise CliError("MIRROR_ARCHIVE_INVALID", f"archive 路径越出技能目录: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                src = tar.extractfile(member)
                if src is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with src, open(target, "wb") as f:
                    shutil.copyfileobj(src, f)
                files += 1
    if files == 0:
        raise CliError("MIRROR_ARCHIVE_INVALID", f"archive 中缺少技能目录: {skill_dir}")
    return target_root


def _safe_tar_parts(name: str) -> tuple[str, ...]:
    if "\x00" in name or "\\" in name or "//" in name:
        raise CliError("MIRROR_ARCHIVE_INVALID", f"archive 成员路径非法: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise CliError("MIRROR_ARCHIVE_INVALID", f"archive 成员路径越界: {name!r}")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if any(":" in part for part in parts):
        # 盘符组件（"C:"）跨平台语义不一：部分 pathlib 版本 joinpath 时会重置或吞掉它，
        # 不能依赖解压后的 is_relative_to 兜底，须在路径层显式拒绝。
        raise CliError("MIRROR_ARCHIVE_INVALID", f"archive 成员路径含保留字符: {name!r}")
    return parts


def _validate_skill_package(skill_root: Path, expected_version: str) -> None:
    skill_md = skill_root / "SKILL.md"
    if not skill_md.is_file() or skill_md.is_symlink():
        raise CliError("SKILL_PACKAGE_INVALID", "镜像技能包缺少普通文件 SKILL.md")
    try:
        parse_skill_meta(skill_md.read_text(encoding="utf-8"), expected_version)
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError("SKILL_PACKAGE_INVALID", "镜像技能包 SKILL.md 无法读取") from exc
