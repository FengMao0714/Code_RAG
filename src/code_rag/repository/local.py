"""本地仓库 provider。

把本地路径解析为 :class:`ResolvedRepo`，不进行任何网络操作。
"""

from __future__ import annotations

import logging
from pathlib import Path

from code_rag.repository.cache import collection_key_for_local
from code_rag.repository.models import (
    SOURCE_TYPE_LOCAL,
    RepoIdentity,
    RepoSource,
    ResolvedRepo,
)

logger = logging.getLogger(__name__)


class LocalRepositoryProvider:
    """本地仓库 provider。"""

    def resolve(self, source: RepoSource) -> ResolvedRepo:
        """解析本地路径为 :class:`ResolvedRepo`。

        Args:
            source: 已识别的本地类型 :class:`RepoSource`。

        Returns:
            :class:`ResolvedRepo`，``root_path`` 为绝对路径。

        Raises:
            FileNotFoundError: 路径不存在。
            NotADirectoryError: 路径不是目录。
        """
        path = Path(source.raw).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"仓库路径不存在: {path}")
        if not path.is_dir():
            raise NotADirectoryError(f"仓库路径不是目录: {path}")
        abs_path = path.resolve()
        canonical_source = str(abs_path)
        identity = RepoIdentity(
            source_type=SOURCE_TYPE_LOCAL,
            display_name=abs_path.name or abs_path.anchor,
            canonical_source=canonical_source,
            ref=None,
            commit=None,
            collection_key=collection_key_for_local(canonical_source),
        )
        logger.debug("本地仓库解析: %s -> %s", source.raw, abs_path)
        return ResolvedRepo(
            source=source,
            identity=identity,
            root_path=abs_path,
            cache_path=None,
        )
