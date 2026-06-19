"""索引流程服务。

把 ``cli.py`` 中 ``index`` 命令的扫描、变更检测、解析、切片、
Embedding、入库、追踪记录等业务编排集中到本模块，CLI 只负责
参数解析和 Rich 展示。

本服务支持本地路径和 Git 远程仓库（统一通过
:mod:`code_rag.repository` 抽象）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_rag.config import Settings, get_settings
from code_rag.indexer import chunker as chunker_mod
from code_rag.indexer import embedder as embedder_mod
from code_rag.indexer import parser as parser_mod
from code_rag.indexer import scanner as scanner_mod
from code_rag.indexer.parser import ParsedSymbol
from code_rag.indexer.scanner import FileEntry
from code_rag.repository import resolve_repo
from code_rag.services.manifest_service import ManifestService
from code_rag.store import index_tracker as tracker_mod
from code_rag.store.index_tracker import ChangeSet
from code_rag.store.vector_store import ChromaStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexResult:
    """单次索引执行的结果摘要。"""

    repo_path: Path
    """实际扫描的本地根目录。"""
    collection_name: str
    scanned_files: int
    added: int
    modified: int
    deleted: int
    chunks_generated: int
    embeddings_generated: int
    had_changes: bool
    source_type: str = "local"
    """``local`` 或 ``git``。"""
    canonical_source: str = ""
    """本地路径或 canonical URL。"""
    ref: str | None = None
    commit: str | None = None
    cache_path: Path | None = None
    collection_key: str = ""

    @property
    def total_changed(self) -> int:
        """发生变更的文件总数。"""
        return self.added + self.modified + self.deleted


# 进度回调签名：``(stage: str, message: str)``。
ProgressCallback = Callable[[str, str], None]


def _noop_progress(_stage: str, _message: str) -> None:
    """默认无操作进度回调。"""
    return None


class IndexService:
    """仓库索引流程服务。

    封装 scanner / parser / chunker / embedder / vector_store / tracker 的协同，
    提供单方法 ``run_index`` 完成一次增量索引。

    Args:
        settings: 应用配置；为 ``None`` 时使用默认配置。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化服务。"""
        self._settings = settings or get_settings()
        self._store = ChromaStore(self._settings)
        self._tracker = tracker_mod.IndexTracker(self._settings)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def run_index(
        self,
        repo_path: str | Path,
        *,
        progress: ProgressCallback | None = None,
        ref: str | None = None,
        refresh: bool = False,
    ) -> IndexResult:
        """对仓库执行一次增量索引。

        流程：

        1. 通过 :func:`resolve_repo` 解析为 :class:`ResolvedRepo`。
        2. 扫描文件。
        3. 检测变更。
        4. 清理已删除文件的 chunk。
        5. 解析 + 切片。
        6. 生成 Embedding。
        7. 写入 ChromaDB。
        8. 更新 tracker。

        Args:
            repo_path: 仓库路径或 git URL。
            progress: 进度回调，签名为 ``(stage, message) -> None``。
                为 ``None`` 时不调用任何回调。
            ref: 可选 git ref（branch / tag / commit）。
            refresh: 是否强制刷新远程仓库缓存。

        Returns:
            :class:`IndexResult` 摘要。

        Raises:
            FileNotFoundError: 路径不存在时抛出。
            NotADirectoryError: 路径不是目录时抛出。
        """
        cb = progress or _noop_progress
        # 解析为 ResolvedRepo
        resolved = resolve_repo(
            str(repo_path),
            ref=ref,
            refresh=refresh,
            settings=self._settings,
        )
        cb(
            "resolve",
            f"已解析仓库: {resolved.identity.display_name} ({resolved.identity.source_type})",
        )

        collection_name = ChromaStore.get_collection_name_from_key(resolved.identity.collection_key)
        cb("scan", "扫描文件...")
        file_entries = scanner_mod.RepoScanner(resolved.root_path).scan()
        cb("scan", f"扫描完成: {len(file_entries)} 个文件")

        cb("detect", "检测变更...")
        changes: ChangeSet = self._tracker.get_changes(resolved, file_entries)
        cb(
            "detect",
            f"变更检测: +{len(changes.added)} ~{len(changes.modified)} -{len(changes.deleted)}",
        )

        if not changes.has_changes:
            return IndexResult(
                repo_path=resolved.root_path,
                collection_name=collection_name,
                scanned_files=len(file_entries),
                added=0,
                modified=0,
                deleted=0,
                chunks_generated=0,
                embeddings_generated=0,
                had_changes=False,
                source_type=resolved.identity.source_type,
                canonical_source=resolved.identity.canonical_source,
                ref=resolved.identity.ref,
                commit=resolved.identity.commit,
                cache_path=resolved.cache_path,
                collection_key=resolved.identity.collection_key,
            )

        # 1. 删除已删除文件 + 已修改文件的旧 chunk
        files_to_clean: list[str] = []
        if changes.deleted:
            files_to_clean.extend(entry.rel_path for entry in changes.deleted)
        if changes.modified:
            files_to_clean.extend(entry.rel_path for entry in changes.modified)
        if files_to_clean:
            self._store.delete_by_files(collection_name, files_to_clean)
            cb("delete", f"已清理 {len(files_to_clean)} 个文件的旧索引")

        # 2. 解析 + 切片
        cb("chunk", "解析代码并切片...")
        all_chunks = self._build_chunks(changes.added + changes.modified, cb)

        if not all_chunks:
            self._tracker.update_tracker(resolved, file_entries)
            manifest_svc = ManifestService(self._settings)
            stats = self._store.get_stats(collection_name)
            manifest_svc.update_manifest(
                repo_path=resolved,
                file_count=len(file_entries),
                chunk_count=int(stats.get("total_chunks", 0)),
                chunk_types=stats.get("chunk_types", {}),
                resolved=resolved,
            )
            return IndexResult(
                repo_path=resolved.root_path,
                collection_name=collection_name,
                scanned_files=len(file_entries),
                added=len(changes.added),
                modified=len(changes.modified),
                deleted=len(changes.deleted),
                chunks_generated=0,
                embeddings_generated=0,
                had_changes=True,
                source_type=resolved.identity.source_type,
                canonical_source=resolved.identity.canonical_source,
                ref=resolved.identity.ref,
                commit=resolved.identity.commit,
                cache_path=resolved.cache_path,
                collection_key=resolved.identity.collection_key,
            )

        # 3. Embedding
        cb("embed", f"生成 Embedding ({len(all_chunks)} 个切片)...")
        embedder = embedder_mod.Embedder.get_instance(self._settings)
        all_embeddings = embedder.embed_texts([c.source for c in all_chunks])
        cb("embed", f"Embedding 完成: {len(all_embeddings)} 个向量")

        # 4. 写入
        cb("upsert", "写入向量数据库...")
        self._store.upsert_chunks(collection_name, all_chunks, all_embeddings)
        cb("upsert", "写入完成")

        # 5. 更新 tracker
        self._tracker.update_tracker(resolved, file_entries)

        # 6. 写入 manifest
        manifest_svc = ManifestService(self._settings)
        stats = self._store.get_stats(collection_name)
        manifest_svc.update_manifest(
            repo_path=resolved,
            file_count=len(file_entries),
            chunk_count=int(stats.get("total_chunks", 0)),
            chunk_types=stats.get("chunk_types", {}),
            resolved=resolved,
        )

        return IndexResult(
            repo_path=resolved.root_path,
            collection_name=collection_name,
            scanned_files=len(file_entries),
            added=len(changes.added),
            modified=len(changes.modified),
            deleted=len(changes.deleted),
            chunks_generated=len(all_chunks),
            embeddings_generated=len(all_embeddings),
            had_changes=True,
            source_type=resolved.identity.source_type,
            canonical_source=resolved.identity.canonical_source,
            ref=resolved.identity.ref,
            commit=resolved.identity.commit,
            cache_path=resolved.cache_path,
            collection_key=resolved.identity.collection_key,
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _build_chunks(
        self,
        entries: list[FileEntry],
        cb: ProgressCallback,
    ) -> list[Any]:
        """对每个文件执行解析 + 切片，汇总所有 chunk。"""
        parser = parser_mod.CodeParser()
        chunker = chunker_mod.CodeChunker(max_chunk_tokens=self._settings.max_chunk_tokens)
        all_chunks: list[Any] = []

        for entry in entries:
            cb("chunk", f"解析: {entry.rel_path}")

            if entry.is_doc:
                try:
                    source = entry.abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    logger.warning("无法读取 %s: %s", entry.rel_path, exc)
                    continue
                doc_sym = ParsedSymbol(
                    file_path=entry.rel_path,
                    language="doc",
                    chunk_type="doc",
                    name=entry.rel_path,
                    start_line=1,
                    end_line=source.count("\n") + 1,
                    parent=None,
                    source=source,
                )
                all_chunks.extend(
                    chunker.chunk([doc_sym], entry.file_hash, full_source=source),
                )
            elif entry.is_code and entry.language:
                symbols = parser.parse_file(entry.abs_path, entry.language, entry.rel_path)
                if not symbols:
                    continue
                try:
                    source = entry.abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    source = None
                all_chunks.extend(
                    chunker.chunk(symbols, entry.file_hash, full_source=source),
                )
            # 其它情况（language 为 None）跳过

        return all_chunks
