"""仓库输入源抽象。

把"仓库"这个概念从 ``Path`` 升级为统一的 :class:`ResolvedRepo`，
使得 CLI 和 service 层都能同时处理本地路径和 Git 远程仓库。

主要组件：

- :class:`RepoSource` / :class:`RepoIdentity` / :class:`ResolvedRepo`:
  数据模型（见 :mod:`code_rag.repository.models`）。
- :func:`parse_repo_source`: 解析用户输入为 :class:`RepoSource`
  （见 :mod:`code_rag.repository.parser`）。
- :func:`resolve_repo`: 把 :class:`RepoSource` 解析为 :class:`ResolvedRepo`，
  本地路径直接 resolve，远程 URL 走 :class:`GitRepositoryProvider`。
- :class:`LocalRepositoryProvider`: 本地仓库 provider。
- :class:`GitRepositoryProvider`: Git 远程仓库 provider（clone / fetch / checkout）。
- :class:`CacheManager`: 远程仓库缓存目录管理。
"""

from code_rag.repository.cache import (
    CacheEntry,
    CacheManager,
    cache_dir_name_for,
    collection_key_for_git,
    collection_key_for_local,
)
from code_rag.repository.git import (
    GitRepositoryError,
    GitRepositoryProvider,
    canonicalize_git_url,
    redact_url,
)
from code_rag.repository.local import LocalRepositoryProvider
from code_rag.repository.models import (
    RepoIdentity,
    RepoSource,
    ResolvedRepo,
)
from code_rag.repository.parser import (
    InvalidRepoSourceError,
    parse_repo_source,
)
from code_rag.repository.resolver import identity_key_for_source, resolve_path, resolve_repo

__all__ = [
    "CacheEntry",
    "CacheManager",
    "GitRepositoryError",
    "GitRepositoryProvider",
    "InvalidRepoSourceError",
    "LocalRepositoryProvider",
    "RepoIdentity",
    "RepoSource",
    "ResolvedRepo",
    "cache_dir_name_for",
    "canonicalize_git_url",
    "collection_key_for_git",
    "collection_key_for_local",
    "identity_key_for_source",
    "parse_repo_source",
    "redact_url",
    "resolve_path",
    "resolve_repo",
]
