"""仓库输入源相关的数据模型。

把 CLI / service 层都依赖的"仓库"抽象为稳定的 dataclass，
避免业务代码再直接处理 ``Path`` / 字符串 / 散乱的字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 仓库来源类型
SOURCE_TYPE_LOCAL = "local"
SOURCE_TYPE_GIT = "git"

VALID_SOURCE_TYPES: frozenset[str] = frozenset({SOURCE_TYPE_LOCAL, SOURCE_TYPE_GIT})


@dataclass(frozen=True)
class RepoSource:
    """用户输入的仓库来源。

    Attributes:
        raw: 原始用户输入字符串。
        kind: 仓库类型，取值 :data:`SOURCE_TYPE_LOCAL` 或 :data:`SOURCE_TYPE_GIT`。
        ref: 可选 ref（branch / tag / commit），仅对 git 类型生效。
    """

    raw: str
    kind: str
    ref: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in VALID_SOURCE_TYPES:
            raise ValueError(f"非法 source kind: {self.kind!r}")


@dataclass(frozen=True)
class RepoIdentity:
    """仓库的稳定身份标识。

    描述一个仓库"是什么"，与它当前在磁盘上的位置解耦，
    因此可以同时支持本地路径、远程 Git URL，以及缓存目录的迁移。

    Attributes:
        source_type: 仓库类型（``local`` / ``git``）。
        display_name: 人类可读的简短名称。
        canonical_source: 规范化来源，本地仓库为绝对路径，git 仓库为 canonical URL。
        ref: 用户指定的 ref（branch / tag / commit）；未指定则为 ``None``。
        commit: 解析到的实际 commit SHA；未解析则为 ``None``。
        collection_key: 稳定的 collection key，被 ChromaDB collection 名称、
            manifest 目录、tracker 目录共享。
    """

    source_type: str
    display_name: str
    canonical_source: str
    ref: str | None
    commit: str | None
    collection_key: str

    def __post_init__(self) -> None:
        if self.source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"非法 source_type: {self.source_type!r}")
        if not self.collection_key:
            raise ValueError("collection_key 不能为空")


@dataclass(frozen=True)
class ResolvedRepo:
    """解析后的仓库：身份 + 实际可扫描的本地路径 + 可选缓存路径。

    Attributes:
        source: 用户输入的 :class:`RepoSource`。
        identity: 仓库的 :class:`RepoIdentity`。
        root_path: 实际给 :class:`RepoScanner` 扫描的本地目录。
        cache_path: 远程仓库缓存目录（仅 git 类型有值）。
    """

    source: RepoSource
    identity: RepoIdentity
    root_path: Path
    cache_path: Path | None = None
