"""Git 远程仓库 provider。

负责 clone / fetch / checkout 远程仓库到本地缓存目录。
默认通过项目已有的 :mod:`gitpython` 依赖调用 git CLI。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from code_rag.repository.cache import CacheManager, collection_key_for_git
from code_rag.repository.models import (
    SOURCE_TYPE_GIT,
    RepoIdentity,
    RepoSource,
    ResolvedRepo,
)

logger = logging.getLogger(__name__)


class GitRepositoryError(RuntimeError):
    """Git 操作失败。"""


def canonicalize_git_url(url: str) -> str:
    """规范化 git URL。

    处理细节：

    - 去除 ``.git`` 后缀的可选情况（保留 ``.git`` 后缀作为标准形式）。
    - 去除尾部 ``/``。
    - 协议统一小写。
    - host 统一小写。
    - scp-like 形式 ``git@host:owner/repo.git`` 转换为 ``ssh://git@host/owner/repo.git``。

    Args:
        url: 原始 URL。

    Returns:
        规范化 URL。
    """
    text = (url or "").strip()
    if not text:
        raise GitRepositoryError("git URL 不能为空")

    # scp-like SSH 形式
    ssh_match = re.match(r"^([a-zA-Z0-9_.\-]+)@([a-zA-Z0-9_.\-]+):(.*)$", text)
    if ssh_match and "://" not in text:
        user, host, path = ssh_match.groups()
        if not path.startswith("/"):
            path = "/" + path
        text = f"ssh://{user}@{host}{path}"

    parsed = urlparse(text)
    if not parsed.scheme:
        raise GitRepositoryError(f"无法解析 git URL: {url}")

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc
    # 拆分 userinfo 与 host
    user = ""
    if "@" in netloc:
        user, _, host_part = netloc.rpartition("@")
        user = user + "@"
        host = host_part.lower()
    else:
        host = netloc.lower()

    path = parsed.path or ""
    if not path.startswith("/"):
        path = "/" + path

    # 规范化 path：去除尾部斜杠
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    canonical = urlunparse((scheme, f"{user}{host}", path, "", "", ""))
    return canonical


class GitRepositoryProvider:
    """Git 仓库 provider。

    Args:
        cache: :class:`CacheManager` 实例。
        clone_depth: 默认 ``--depth`` 值；``0`` 表示完整克隆。
        allow_private: 是否允许私有仓库（暂未真正支持鉴权，仅作为配置项）。
    """

    def __init__(
        self,
        cache: CacheManager,
        *,
        clone_depth: int = 1,
        allow_private: bool = False,
    ) -> None:
        """初始化 provider。"""
        self._cache = cache
        self._clone_depth = int(clone_depth) if clone_depth else 0
        self._allow_private = bool(allow_private)

    @property
    def cache(self) -> CacheManager:
        """关联的 :class:`CacheManager`。"""
        return self._cache

    def resolve(
        self,
        source: RepoSource,
        *,
        refresh: bool = False,
    ) -> ResolvedRepo:
        """解析 git URL 为 :class:`ResolvedRepo`。

        流程：

        1. 规范化 URL。
        2. 计算缓存目录。
        3. 缓存不存在或 ``refresh=True`` 时执行 clone；否则 fetch。
        4. 切换到指定 ref（branch / tag / commit），并记录 commit SHA。

        Args:
            source: 已识别的 git 类型 :class:`RepoSource`。
            refresh: 是否强制刷新远程仓库。

        Returns:
            :class:`ResolvedRepo`，``root_path`` 为缓存中的 worktree 目录。

        Raises:
            GitRepositoryError: 任何 git 操作失败。
        """
        canonical = canonicalize_git_url(source.raw)
        cache_dir = self._cache.cache_dir_for(canonical)
        worktree = cache_dir / "worktree"

        existed = worktree.is_dir() and (worktree / ".git").exists()
        if not existed:
            self._clone(canonical, worktree, ref=source.ref)
        else:
            # 缓存已存在：refresh=True 时先尝试 fetch + checkout（不重新 clone），
            # 这样在 Windows 上可以避免 git pack 文件句柄未释放的问题。
            self._fetch_and_checkout(canonical, worktree, ref=source.ref)

        commit = self._resolve_commit(worktree)
        identity = RepoIdentity(
            source_type=SOURCE_TYPE_GIT,
            display_name=worktree.name or canonical.rsplit("/", 1)[-1],
            canonical_source=canonical,
            ref=source.ref,
            commit=commit,
            collection_key=collection_key_for_git(canonical, source.ref),
        )
        self._cache.update_entry(
            cache_dir,
            canonical_url=canonical,
            ref=source.ref,
            commit=commit,
            is_initial=not existed,
        )
        logger.info(
            "Git 仓库解析: %s -> %s (commit=%s)",
            source.raw,
            worktree,
            commit,
        )
        return ResolvedRepo(
            source=source,
            identity=identity,
            root_path=worktree,
            cache_path=cache_dir,
        )

    # ------------------------------------------------------------------
    # 内部：git 操作
    # ------------------------------------------------------------------

    def _clone(self, url: str, worktree: Path, *, ref: str | None) -> None:
        """执行 git clone。"""
        try:
            import git  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 防御
            raise GitRepositoryError("缺少 GitPython 依赖，请执行: uv add gitpython") from exc

        if worktree.exists():
            # refresh 模式：先把旧 worktree 清掉再 clone
            import shutil

            shutil.rmtree(worktree.parent, ignore_errors=True)

        worktree.parent.mkdir(parents=True, exist_ok=True)
        logger.info("正在 clone: %s -> %s", url, worktree)
        try:
            clone_kwargs: dict = {}
            if self._clone_depth and self._clone_depth > 0:
                clone_kwargs["depth"] = self._clone_depth
            if ref and self._looks_like_branch_or_tag(ref):
                # 远端 branch 或 tag：浅克隆时直接指定 branch
                if self._clone_depth and self._clone_depth > 0:
                    clone_kwargs["branch"] = ref
            git.Repo.clone_from(url, str(worktree), **clone_kwargs)
        except Exception as exc:
            raise GitRepositoryError(f"git clone 失败: {url} — {exc}") from exc

        if ref and not (self._clone_depth and self._clone_depth > 0 and "branch" in clone_kwargs):
            # ref 是 commit 或 tag 时需要 checkout
            self._checkout(worktree, ref)

    def _fetch_and_checkout(self, url: str, worktree: Path, *, ref: str | None) -> None:
        """对已有缓存执行 fetch + checkout。"""
        try:
            import git  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 防御
            raise GitRepositoryError("缺少 GitPython 依赖，请执行: uv add gitpython") from exc

        try:
            repo = git.Repo(str(worktree))
        except Exception as exc:
            raise GitRepositoryError(f"无法打开本地 git 仓库: {worktree} — {exc}") from exc

        try:
            for remote in repo.remotes:
                try:
                    remote.fetch()
                except Exception as exc:  # pragma: no cover - 防御
                    logger.warning("git fetch 失败 (%s): %s", remote, exc)
        except Exception as exc:
            logger.warning("git fetch 出错: %s", exc)

        if ref:
            self._checkout(worktree, ref)
        else:
            # 没有指定 ref：尝试切回默认分支（origin/HEAD -> main/master）
            try:
                default = self._detect_default_branch(repo)
                if default:
                    self._checkout(worktree, default)
            except Exception as exc:  # pragma: no cover - 防御
                logger.debug("检测默认分支失败: %s", exc)

    def _checkout(self, worktree: Path, ref: str) -> None:
        """切换到指定 ref（branch / tag / commit）。"""
        try:
            import git  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 防御
            raise GitRepositoryError("缺少 GitPython 依赖，请执行: uv add gitpython") from exc

        try:
            repo = git.Repo(str(worktree))
        except Exception as exc:
            raise GitRepositoryError(f"无法打开本地 git 仓库: {worktree} — {exc}") from exc

        # 先尝试浅克隆 + commit SHA 的情况
        if self._looks_like_commit(ref):
            try:
                repo.git.checkout(ref)
                return
            except Exception as exc:
                raise GitRepositoryError(f"无法 checkout commit {ref!r}: {exc}") from exc

        # 尝试远端 branch（origin/<ref>）
        candidate_refs = [ref, f"origin/{ref}"]
        for candidate in candidate_refs:
            try:
                if candidate in repo.refs:
                    repo.git.checkout(candidate)
                    return
            except Exception:
                continue

        # 尝试 tag
        try:
            if ref in repo.tags:
                repo.git.checkout(f"tags/{ref}")
                return
        except Exception:
            pass

        raise GitRepositoryError(f"无法找到 ref: {ref!r}（不是 commit，也不是 branch / tag）")

    def _resolve_commit(self, worktree: Path) -> str | None:
        """读取当前 worktree 的 HEAD commit SHA。"""
        try:
            import git  # type: ignore[import-not-found]
        except ImportError:
            return None
        try:
            repo = git.Repo(str(worktree))
            return repo.head.commit.hexsha
        except Exception:
            return None

    @staticmethod
    def _looks_like_commit(ref: str) -> bool:
        """判断 ref 是否是 commit SHA（7~40 位十六进制）。"""
        return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref))

    @staticmethod
    def _looks_like_branch_or_tag(ref: str) -> bool:
        """粗略判断 ref 是否像 branch / tag 名（不包含 ``/`` 也行，但更宽松）。"""
        if not ref:
            return False
        # 排除 commit SHA
        if GitRepositoryProvider._looks_like_commit(ref):
            return False
        return True

    @staticmethod
    def _detect_default_branch(repo: object) -> str | None:
        """尝试获取默认 branch。"""
        try:
            # 优先 origin/HEAD
            try:
                head_ref = repo.remotes.origin.refs.HEAD  # type: ignore[attr-defined]
                return head_ref.reference.name
            except Exception:
                pass
            for name in ("main", "master", "develop"):
                try:
                    if f"origin/{name}" in [r.name for r in repo.refs]:
                        return name
                except Exception:
                    continue
        except Exception:
            return None
        return None
