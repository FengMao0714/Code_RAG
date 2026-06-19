"""ChromaDB 向量存储封装。

管理 ChromaDB 持久化客户端，支持按仓库路径隔离 collection，
提供 chunk 的 upsert、向量检索、按文件删除等操作。

Embedding 由外部 :class:`~code_rag.indexer.embedder.Embedder` 生成，
本模块仅负责存储和检索，不直接调用 Embedding 模型。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from code_rag.config import Settings, get_settings
from code_rag.indexer.chunker import CodeChunk

logger = logging.getLogger(__name__)

# ChromaDB >=0.5 抛出 NotFoundError 而非 ValueError
try:
    from chromadb.errors import NotFoundError as _ChromaNotFoundError
except (ImportError, ModuleNotFoundError):
    _ChromaNotFoundError = ValueError  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# 检索结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResult:
    """向量检索的单条结果。

    Attributes:
        chunk: 匹配的代码切片。
        score: 与查询向量的距离（越小越相关）。
    """

    chunk: CodeChunk
    score: float


# ---------------------------------------------------------------------------
# 向量存储
# ---------------------------------------------------------------------------


class ChromaStore:
    """ChromaDB 向量存储封装。

    管理持久化客户端和 collection 的生命周期。
    每个仓库对应一个独立的 collection，名称基于仓库路径的 SHA-256 哈希生成。

    用法::

        store = ChromaStore()
        store.upsert_chunks(collection_name, chunks, embeddings)
        results = store.query(collection_name, query_embedding, top_k=8)

    Args:
        settings: 应用配置；为 ``None`` 时使用默认配置。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化向量存储。

        Args:
            settings: 应用配置。
        """
        self._settings = settings or get_settings()
        self._client = self._create_client()

    def _create_client(self) -> object:
        """创建 ChromaDB 持久化客户端。

        Returns:
            ``chromadb.PersistentClient`` 实例。

        Raises:
            RuntimeError: chromadb 未安装时抛出。
        """
        try:
            import chromadb
        except ImportError:
            raise RuntimeError("chromadb 未安装。请执行: uv add chromadb")

        persist_dir = self._settings.chroma_persist_path
        persist_dir.mkdir(parents=True, exist_ok=True)

        client = chromadb.PersistentClient(path=str(persist_dir))
        logger.info("ChromaDB 持久化目录: %s", persist_dir)
        return client

    # ------------------------------------------------------------------
    # Collection 管理
    # ------------------------------------------------------------------

    @staticmethod
    def get_collection_name(repo_path: str | Path) -> str:
        """根据仓库路径生成 collection 名称。

        使用仓库绝对路径的 SHA-256 哈希前 12 位作为后缀，
        确保不同仓库的 collection 相互隔离。

        Args:
            repo_path: 仓库路径。

        Returns:
            collection 名称，如 ``code-rag-a1b2c3d4e5f6``。
        """
        abs_path = str(Path(repo_path).resolve())
        digest = hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:12]
        return f"code-rag-{digest}"

    @staticmethod
    def get_collection_name_from_key(collection_key: str) -> str:
        """根据稳定的 :class:`RepoIdentity.collection_key` 生成 collection 名称。

        支持 local path 和 git URL 统一入口。``collection_key`` 应来自
        :class:`~code_rag.repository.models.RepoIdentity`，例如：

        - ``a1b2c3d4e5f6``（本地路径，与 :meth:`get_collection_name` 完全相同）
        - ``git-12345678-main``（git 仓库 + ref）

        特殊处理：如果是 12 位十六进制字符串（来自本地路径），
        直接返回 ``code-rag-<key>``，与老 :meth:`get_collection_name` 行为一致，
        保证老的本地索引不会失效。

        Args:
            collection_key: 来自 :class:`RepoIdentity` 的稳定 key。

        Returns:
            规范化的 collection 名称。
        """
        if not collection_key:
            raise ValueError("collection_key 不能为空")
        # 本地路径：12 位十六进制，与老逻辑完全一致
        if len(collection_key) == 12 and all(c in "0123456789abcdef" for c in collection_key):
            return f"code-rag-{collection_key}"
        digest = hashlib.sha256(collection_key.encode("utf-8")).hexdigest()[:12]
        return f"code-rag-{digest}"

    def _get_collection(self, name: str) -> object | None:
        """只读获取 collection，不存在时返回 None。

        用于 query 等只读操作，避免产生持久化副作用。

        Args:
            name: collection 名称。

        Returns:
            ChromaDB Collection 对象，不存在时返回 ``None``。
        """
        try:
            return self._client.get_collection(name=name)  # type: ignore[union-attr]
        except _ChromaNotFoundError:
            return None

    def get_or_create_collection(self, name: str) -> object:
        """获取或创建指定名称的 collection。

        Args:
            name: collection 名称。

        Returns:
            ChromaDB Collection 对象。
        """
        collection = self._client.get_or_create_collection(  # type: ignore[union-attr]
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.debug("已获取 collection: %s (size=%d)", name, collection.count())
        return collection

    def delete_collection(self, name: str) -> None:
        """删除指定的 collection。

        Args:
            name: collection 名称。
        """
        try:
            self._client.delete_collection(name=name)  # type: ignore[union-attr]
            logger.info("已删除 collection: %s", name)
        except _ChromaNotFoundError:
            logger.warning("collection 不存在，跳过删除: %s", name)

    # ------------------------------------------------------------------
    # 写入操作
    # ------------------------------------------------------------------

    def upsert_chunks(
        self,
        collection_name: str,
        chunks: list[CodeChunk],
        embeddings: list[list[float]],
    ) -> None:
        """将代码切片及其嵌入向量写入 collection。

        使用 chunk 的 ``file_path`` + ``start_line`` 作为唯一 ID，
        支持幂等写入（相同 ID 自动覆盖）。

        Args:
            collection_name: collection 名称。
            chunks: 代码切片列表。
            embeddings: 与 ``chunks`` 等长的嵌入向量列表。

        Raises:
            ValueError: chunks 与 embeddings 长度不匹配时抛出。
        """
        if len(chunks) != len(embeddings):
            raise ValueError(f"chunks ({len(chunks)}) 与 embeddings ({len(embeddings)}) 长度不匹配")
        if not chunks:
            return

        collection = self.get_or_create_collection(collection_name)

        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        vecs: list[list[float]] = []
        seen_ids: set[str] = set()

        for chunk, embedding in zip(chunks, embeddings):
            # 用 chunk_type + source + start_line 生成哈希，
            # 避免 module_summary 与同起始行的符号产生重复 ID
            id_src = f"{chunk.chunk_type}:{chunk.source}:{chunk.start_line}"
            chunk_id = f"{chunk.file_path}:{hashlib.sha256(id_src.encode()).hexdigest()[:12]}"
            if chunk_id in seen_ids:
                logger.debug("跳过重复 chunk: %s", chunk_id)
                continue
            seen_ids.add(chunk_id)
            ids.append(chunk_id)
            docs.append(chunk.source)
            metas.append(self._extract_metadata(chunk))
            vecs.append(self._ensure_list(embedding))

        batch_size = 500
        for i in range(0, len(ids), batch_size):
            batch_end = i + batch_size
            collection.upsert(
                ids=ids[i:batch_end],
                embeddings=vecs[i:batch_end],
                documents=docs[i:batch_end],
                metadatas=metas[i:batch_end],
            )

        logger.info(
            "已写入 %d 条 chunk 到 collection '%s'",
            len(chunks),
            collection_name,
        )

    # ------------------------------------------------------------------
    # 检索操作
    # ------------------------------------------------------------------

    def query(
        self,
        collection_name: str,
        embedding: list[float],
        *,
        top_k: int = 8,
        max_distance: float | None = None,
    ) -> list[SearchResult]:
        """根据嵌入向量检索最相似的代码切片。

        Args:
            collection_name: collection 名称。
            embedding: 查询嵌入向量。
            top_k: 返回的最大结果数。
            max_distance: 最大距离阈值；超过此值的结果将被过滤。
                使用 cosine 距离时，0.0 = 完全相同，2.0 = 完全相反。

        Returns:
            按相关性排序的 :class:`SearchResult` 列表（距离由小到大）。
        """
        collection = self._get_collection(collection_name)
        if collection is None:
            logger.warning("collection '%s' 不存在，无法检索", collection_name)
            return []

        if collection.count() == 0:
            logger.warning("collection '%s' 为空，无法检索", collection_name)
            return []

        query_params: dict = {
            "query_embeddings": [self._ensure_list(embedding)],
            "n_results": min(top_k, collection.count()),
            "include": ["distances", "documents", "metadatas"],
        }

        results = collection.query(**query_params)

        # 解包 ChromaDB 返回的嵌套列表（每个字段被包在外层 list 中）
        result_ids = results.get("ids", [[]])[0]
        result_distances = results.get("distances", [[]])[0]
        result_docs = results.get("documents", [[]])[0]
        result_metas = results.get("metadatas", [[]])[0]

        search_results: list[SearchResult] = []
        for idx in range(len(result_ids)):
            distance = result_distances[idx]

            if max_distance is not None and distance > max_distance:
                continue

            chunk = self._reconstruct_chunk(
                metadata=result_metas[idx],
                source=result_docs[idx],
            )
            search_results.append(SearchResult(chunk=chunk, score=distance))

        logger.info(
            "检索到 %d 条结果 (collection='%s', top_k=%d)",
            len(search_results),
            collection_name,
            top_k,
        )
        return search_results

    # ------------------------------------------------------------------
    # 删除操作
    # ------------------------------------------------------------------

    def delete_by_files(
        self,
        collection_name: str,
        file_paths: list[str],
    ) -> None:
        """删除指定文件的所有 chunk。

        增量更新时用于清理已删除文件对应的 chunk。

        Args:
            collection_name: collection 名称。
            file_paths: 要删除的文件相对路径列表。
        """
        if not file_paths:
            return

        collection = self.get_or_create_collection(collection_name)

        for file_path in file_paths:
            try:
                collection.delete(where={"file_path": file_path})
            except ValueError:
                logger.debug("文件路径无匹配 chunk，跳过: %s", file_path)

        logger.info("已从 '%s' 删除 %d 个文件的 chunk", collection_name, len(file_paths))

    def get_stats(self, collection_name: str) -> dict:
        """获取 collection 的统计信息。

        Args:
            collection_name: collection 名称。

        Returns:
            包含 ``total_chunks`` 等统计字段的字典。
        """
        try:
            collection = self._client.get_collection(name=collection_name)  # type: ignore[union-attr]
        except _ChromaNotFoundError:
            return {"total_chunks": 0, "exists": False}

        total = collection.count()

        # 按 chunk_type 统计（采样前 1000 条，避免全量扫描）
        sample_size = min(total, 1000)
        type_counts: dict[str, int] = {}
        if sample_size > 0:
            sample = collection.get(
                limit=sample_size,
                include=["metadatas"],
            )
            for meta in sample.get("metadatas", []):
                ctype = meta.get("chunk_type", "unknown")
                type_counts[ctype] = type_counts.get(ctype, 0) + 1

        return {
            "exists": True,
            "total_chunks": total,
            "chunk_types": type_counts,
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_list(embedding: object) -> list[float]:
        """确保嵌入向量为 ``list[float]`` 类型。

        兼容 numpy 数组和 PyTorch 张量输入。

        Args:
            embedding: 嵌入向量（list / numpy array / tensor）。

        Returns:
            纯 Python list。
        """
        if isinstance(embedding, list):
            return embedding
        # numpy.ndarray 或 torch.Tensor
        if hasattr(embedding, "tolist"):
            return embedding.tolist()  # type: ignore[union-attr]
        return list(embedding)  # type: ignore[arg-type]

    @staticmethod
    def _extract_metadata(chunk: CodeChunk) -> dict:
        """从 CodeChunk 提取 ChromaDB 兼容的 metadata。

        ChromaDB 仅支持 str / int / float / bool 类型的 metadata 值，
        因此 ``parent``（``None`` → ``""``）和 ``metadata``（``dict`` → JSON 字符串）
        需要做类型转换。

        Args:
            chunk: 代码切片。

        Returns:
            ChromaDB 兼容的 metadata 字典。
        """
        return {
            "file_path": chunk.file_path,
            "language": chunk.language,
            "chunk_type": chunk.chunk_type,
            "name": chunk.name,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "parent": chunk.parent or "",
            "file_hash": chunk.file_hash,
            "token_count": chunk.token_count,
            "extra_metadata": json.dumps(chunk.metadata, ensure_ascii=False),
        }

    @staticmethod
    def _reconstruct_chunk(metadata: dict, source: str) -> CodeChunk:
        """从 ChromaDB 存储的 metadata 和 document 重建 CodeChunk。

        Args:
            metadata: ChromaDB metadata 字典。
            source: 切片的源代码文本。

        Returns:
            重建的 :class:`CodeChunk` 实例。
        """
        extra_metadata_raw = metadata.get("extra_metadata", "{}")
        try:
            extra_metadata = json.loads(extra_metadata_raw) if extra_metadata_raw else {}
        except (TypeError, json.JSONDecodeError):
            logger.warning("无法解析 chunk extra_metadata，已忽略: %r", extra_metadata_raw)
            extra_metadata = {}

        return CodeChunk(
            file_path=metadata.get("file_path", ""),
            language=metadata.get("language", ""),
            chunk_type=metadata.get("chunk_type", ""),
            name=metadata.get("name", ""),
            start_line=metadata.get("start_line", 0),
            end_line=metadata.get("end_line", 0),
            parent=metadata.get("parent") or None,
            file_hash=metadata.get("file_hash", ""),
            source=source,
            token_count=metadata.get("token_count", 0),
            metadata=extra_metadata,
        )
