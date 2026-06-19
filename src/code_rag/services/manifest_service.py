"""仓库 manifest 服务。

在 ``index_tracker_path`` 中为每个已索引仓库维护一份 ``manifest.json``，
记录仓库身份、collection 名称、最后索引时间、文件数、
chunk 数、模型与关键配置等。

``list`` / ``status`` 命令通过本服务读取 manifest，
``remove`` 命令在删除索引时同时删除 manifest。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from code_rag.config import Settings, get_settings
from code_rag.repository import ResolvedRepo, identity_key_for_source, resolve_repo
from code_rag.store.vector_store import ChromaStore

logger = logging.getLogger(__name__)

_MANIFEST_FILENAME = "manifest.json"

# 兼容老 manifest：旧 manifest 内的 source_type 可能是 "local" 或缺失。
DEFAULT_SOURCE_TYPE = "local"


@dataclass
class ManifestEntry:
    """单个仓库的 manifest 记录。

    Attributes:
        repo_path: 仓库的本地路径（git 仓库为缓存中的 worktree 路径）。
        repo_hash: 12 位稳定 key，兼容老逻辑；与 collection_key 保持一致。
        collection_name: ChromaDB collection 名称。
        collection_key: 稳定的 :class:`RepoIdentity.collection_key`。
        source_type: ``local`` / ``git``。
        canonical_source: 本地路径或 canonical git URL。
        display_name: 人类可读名称。
        ref: git ref。
        commit: 解析到的 commit SHA。
        cache_path: 远程仓库缓存目录（仅 git 有值）。
        last_indexed_at: 最后索引时间（ISO 8601）。
        file_count: tracker 记录的文件数。
        chunk_count: ChromaDB 中的 chunk 数。
        chunk_types: chunk_type 分布。
        embedding_model: 索引时使用的 Embedding 模型。
        llm_model: 配置中的 LLM 模型。
        retrieval_top_k: 检索 top_k。
        retrieval_score_threshold: 检索距离阈值。
        max_chunk_tokens: 切片 token 上限。
    """

    repo_path: str
    repo_hash: str
    collection_name: str
    collection_key: str = ""
    source_type: str = DEFAULT_SOURCE_TYPE
    canonical_source: str = ""
    display_name: str = ""
    ref: str | None = None
    commit: str | None = None
    cache_path: str | None = None
    last_indexed_at: str = ""
    file_count: int = 0
    chunk_count: int = 0
    chunk_types: dict[str, int] = field(default_factory=dict)
    embedding_model: str = ""
    llm_model: str = ""
    retrieval_top_k: int = 8
    retrieval_score_threshold: float = 0.7
    max_chunk_tokens: int = 512

    @classmethod
    def from_dict(cls, data: dict) -> ManifestEntry:
        """从字典构造。"""
        return cls(
            repo_path=str(data.get("repo_path", "")),
            repo_hash=str(data.get("repo_hash", "")),
            collection_name=str(data.get("collection_name", "")),
            collection_key=str(data.get("collection_key", "")),
            source_type=str(data.get("source_type", DEFAULT_SOURCE_TYPE)),
            canonical_source=str(data.get("canonical_source", "")),
            display_name=str(data.get("display_name", "")),
            ref=data.get("ref"),
            commit=data.get("commit"),
            cache_path=data.get("cache_path"),
            last_indexed_at=str(data.get("last_indexed_at", "")),
            file_count=int(data.get("file_count", 0)),
            chunk_count=int(data.get("chunk_count", 0)),
            chunk_types=dict(data.get("chunk_types", {})),
            embedding_model=str(data.get("embedding_model", "")),
            llm_model=str(data.get("llm_model", "")),
            retrieval_top_k=int(data.get("retrieval_top_k", 8)),
            retrieval_score_threshold=float(data.get("retrieval_score_threshold", 0.7)),
            max_chunk_tokens=int(data.get("max_chunk_tokens", 512)),
        )

    def to_dict(self) -> dict:
        """转为可序列化的字典。"""
        return asdict(self)


# 兼容类型：``str | Path | ResolvedRepo``
ManifestKey = str | Path | ResolvedRepo


class ManifestService:
    """仓库 manifest 读写服务。

    Args:
        settings: 应用配置；为 ``None`` 时使用默认配置。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化服务。"""
        self._settings = settings or get_settings()
        self._store = ChromaStore(self._settings)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def update_manifest(
        self,
        repo_path: ManifestKey,
        *,
        file_count: int,
        chunk_count: int,
        chunk_types: dict[str, int] | None = None,
        resolved: ResolvedRepo | None = None,
    ) -> ManifestEntry:
        """写入或更新指定仓库的 manifest。

        Args:
            repo_path: 仓库标识（``str | Path | ResolvedRepo``）。
            file_count: tracker 记录的文件数。
            chunk_count: ChromaDB 中的 chunk 数。
            chunk_types: chunk 类型分布。
            resolved: 预解析好的 :class:`ResolvedRepo`；为 ``None`` 时按
                ``repo_path`` 自动解析（仅本地路径会被解析为 ResolvedRepo）。

        Returns:
            新写入的 :class:`ManifestEntry`。
        """
        resolved_obj = self._ensure_resolved(repo_path, resolved)
        collection_name = ChromaStore.get_collection_name_from_key(
            resolved_obj.identity.collection_key
        )
        entry = ManifestEntry(
            repo_path=str(resolved_obj.root_path),
            repo_hash=resolved_obj.identity.collection_key,
            collection_name=collection_name,
            collection_key=resolved_obj.identity.collection_key,
            source_type=resolved_obj.identity.source_type,
            canonical_source=resolved_obj.identity.canonical_source,
            display_name=resolved_obj.identity.display_name,
            ref=resolved_obj.identity.ref,
            commit=resolved_obj.identity.commit,
            cache_path=str(resolved_obj.cache_path) if resolved_obj.cache_path else None,
            last_indexed_at=datetime.now().isoformat(timespec="seconds"),
            file_count=file_count,
            chunk_count=chunk_count,
            chunk_types=dict(chunk_types or {}),
            embedding_model=self._settings.embedding_model,
            llm_model=self._settings.llm_model,
            retrieval_top_k=self._settings.retrieval_top_k,
            retrieval_score_threshold=self._settings.retrieval_score_threshold,
            max_chunk_tokens=self._settings.max_chunk_tokens,
        )
        self._write(entry)
        return entry

    def list_manifests(self) -> list[ManifestEntry]:
        """读取所有仓库的 manifest 记录。"""
        manifests: list[ManifestEntry] = []
        if not self._settings.index_tracker_path.exists():
            return manifests
        for hash_dir in self._settings.index_tracker_path.iterdir():
            manifest_path = hash_dir / _MANIFEST_FILENAME
            if not manifest_path.is_file():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifests.append(ManifestEntry.from_dict(data))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("无法读取 manifest %s: %s", manifest_path, exc)
        manifests.sort(key=lambda e: e.last_indexed_at, reverse=True)
        return manifests

    def get_manifest(self, repo_path: ManifestKey) -> ManifestEntry | None:
        """读取指定仓库的 manifest。

        Args:
            repo_path: 仓库标识。

        Returns:
            找到则返回 :class:`ManifestEntry`，否则返回 ``None``。
        """
        resolved = self._ensure_resolved(repo_path)
        manifest_path = self._manifest_path_for(resolved.identity.collection_key)
        if not manifest_path.is_file():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return ManifestEntry.from_dict(data)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("无法读取 manifest %s: %s", manifest_path, exc)
            return None

    def remove_manifest(self, repo_path: ManifestKey) -> bool:
        """删除指定仓库的 manifest。

        Args:
            repo_path: 仓库标识。

        Returns:
            是否删除了 manifest。
        """
        resolved = self._ensure_resolved(repo_path)
        manifest_path = self._manifest_path_for(resolved.identity.collection_key)
        if not manifest_path.is_file():
            return False
        try:
            manifest_path.unlink()
            logger.info("已删除 manifest: %s", manifest_path)
            return True
        except OSError as exc:
            logger.warning("无法删除 manifest %s: %s", manifest_path, exc)
            return False

    def get_status(
        self,
        repo_path: ManifestKey,
        *,
        ref: str | None = None,
        resolved: ResolvedRepo | None = None,
    ) -> tuple[ManifestEntry | None, dict]:
        """读取 manifest 与当前 ChromaDB 统计。

        优先使用 :func:`identity_key_for_source` 仅计算 key，避免对
        远程 Git URL 触发 clone / fetch。仅当 key 查不到 manifest 且
        需要写入时才回退到完整解析。

        Args:
            repo_path: 仓库标识。
            ref: 可选 git ref，仅当 ``repo_path`` 不是 :class:`ResolvedRepo` 时生效。
            resolved: 预解析的 :class:`ResolvedRepo`。

        Returns:
            ``(manifest, store_stats)`` 元组。
        """
        if isinstance(repo_path, ResolvedRepo):
            resolved_obj = repo_path
        elif resolved is not None:
            resolved_obj = resolved
        else:
            # 只计算 key，不 clone
            collection_key = identity_key_for_source(str(repo_path), ref, settings=self._settings)
            collection_name = ChromaStore.get_collection_name_from_key(collection_key)
            manifest = self._get_manifest_by_key(collection_key)
            store_stats = self._store.get_stats(collection_name)
            return manifest, store_stats

        manifest = self.get_manifest(resolved_obj)
        collection_name = ChromaStore.get_collection_name_from_key(
            resolved_obj.identity.collection_key
        )
        store_stats = self._store.get_stats(collection_name)
        return manifest, store_stats

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get_manifest_by_key(self, collection_key: str) -> ManifestEntry | None:
        """根据 collection_key 直接读取 manifest，不触发 resolve。"""
        manifest_path = self._manifest_path_for(collection_key)
        if not manifest_path.is_file():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return ManifestEntry.from_dict(data)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("无法读取 manifest %s: %s", manifest_path, exc)
            return None

    def remove_manifest_by_key(self, collection_key: str) -> bool:
        """根据 collection_key 删除 manifest，不触发 resolve。

        Args:
            collection_key: 稳定的 collection key。

        Returns:
            是否删除了 manifest。
        """
        manifest_path = self._manifest_path_for(collection_key)
        if not manifest_path.is_file():
            return False
        try:
            manifest_path.unlink()
            logger.info("已删除 manifest: %s", manifest_path)
            return True
        except OSError as exc:
            logger.warning("无法删除 manifest %s: %s", manifest_path, exc)
            return False

    def _manifest_path_for(self, collection_key: str) -> Path:
        """根据 collection_key 返回 manifest.json 路径。"""
        return self._settings.index_tracker_path / collection_key / _MANIFEST_FILENAME

    def _write(self, entry: ManifestEntry) -> None:
        """写入 manifest.json。"""
        path = self._manifest_path_for(entry.collection_key or entry.repo_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(entry.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.debug("已写入 manifest: %s", path)

    def _ensure_resolved(
        self,
        repo_path: ManifestKey,
        resolved: ResolvedRepo | None = None,
    ) -> ResolvedRepo:
        """把入参规范化为 :class:`ResolvedRepo`。

        - 已是 :class:`ResolvedRepo` → 直接返回。
        - ``str | Path`` → 调用 :func:`resolve_repo`。
        """
        if isinstance(repo_path, ResolvedRepo):
            return repo_path
        if resolved is not None:
            return resolved
        return resolve_repo(str(repo_path), settings=self._settings)
