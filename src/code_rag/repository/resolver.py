"""仓库统一解析入口。

业务代码应使用 :func:`resolve_repo` 而非直接调用 provider，
以便后续扩展新的输入源类型。
"""

from __future__ import annotations

import logging
from pathlib import Path

from code_rag.config import Settings, get_settings
from code_rag.repository.cache import CacheManager
from code_rag.repository.git import GitRepositoryProvider
from code_rag.repository.local import LocalRepositoryProvider
from code_rag.repository.models import (
    RepoSource,
    ResolvedRepo,
)
from code_rag.repository.parser import parse_repo_source

logger = logging.getLogger(__name__)


def resolve_repo(
    source: str,
    *,
    ref: str | None = None,
    refresh: bool = False,
    settings: Settings | None = None,
) -> ResolvedRepo:
    """解析用户输入为 :class:`ResolvedRepo`。

    流程：

    1. 调用 :func:`parse_repo_source` 识别本地路径 / git URL。
    2. 根据 ``kind`` 派发到对应 provider。
    3. 返回 :class:`ResolvedRepo`，供 :class:`IndexService` /
       :class:`QueryService` / :class:`ManifestService` 使用。

    Args:
        source: 原始用户输入字符串。
        ref: 可选 git ref（branch / tag / commit）。
        refresh: 是否强制刷新远程缓存。
        settings: 应用配置；为 ``None`` 时使用默认配置。

    Returns:
        :class:`ResolvedRepo`。

    Raises:
        FileNotFoundError: 本地路径不存在。
        NotADirectoryError: 本地路径不是目录。
        GitRepositoryError: Git 操作失败。
    """
    cfg = settings or get_settings()
    repo_source = parse_repo_source(source)
    if ref is not None:
        # 构造新的 RepoSource 时保留 ref
        repo_source = RepoSource(raw=repo_source.raw, kind=repo_source.kind, ref=ref)

    if repo_source.kind == "local":
        provider = LocalRepositoryProvider()
        return provider.resolve(repo_source)

    if repo_source.kind == "git":
        cache = CacheManager(cfg.repo_cache_dir)
        provider = GitRepositoryProvider(
            cache,
            clone_depth=cfg.git_clone_depth,
            allow_private=cfg.allow_private_git,
        )
        return provider.resolve(repo_source, refresh=refresh)

    raise ValueError(f"未知 source kind: {repo_source.kind!r}")


def resolve_path(path: str | Path, settings: Settings | None = None) -> ResolvedRepo:
    """便捷函数：把 ``Path | str`` 解析为本地 :class:`ResolvedRepo`。

    用于服务层在已经知道是本地路径时跳过 URL 解析。
    """
    source = RepoSource(raw=str(path), kind="local", ref=None)
    return LocalRepositoryProvider().resolve(source)
