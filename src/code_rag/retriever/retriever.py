"""向量检索与上下文组装模块。

负责将用户问题转换为 Embedding 向量，从 ChromaDB 中检索最相关的代码切片，
并将检索结果格式化为 LLM 可消费的上下文字符串。

主要组件：

- :class:`Retriever`: 向量检索器，封装 Embedder + ChromaStore 的检索流程。
- :class:`ContextBuilder`: 上下文组装器，将检索结果格式化为 Prompt 所需的上下文。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from code_rag.config import Settings, get_settings
from code_rag.generator.prompts import CONTEXT_CHUNK_TEMPLATE
from code_rag.indexer.chunker import CodeChunk
from code_rag.indexer.embedder import Embedder
from code_rag.store.vector_store import ChromaStore, SearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 检索结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalResult:
    """单次检索的完整结果。

    Attributes:
        question: 用户的原始问题。
        chunks: 检索到的代码切片列表（按相关性排序）。
        context: 格式化后的上下文字符串，可直接用于 Prompt。
        scores: 与 ``chunks`` 等长的距离分数列表（越小越相关）。
    """

    question: str
    chunks: list[CodeChunk]
    context: str
    scores: list[float]


# ---------------------------------------------------------------------------
# 向量检索器
# ---------------------------------------------------------------------------


class Retriever:
    """向量检索器。

    封装 Embedder 和 ChromaStore，提供基于语义相似度的代码检索能力。

    工作流程：

    1. 使用 Embedder 将查询文本转换为向量
    2. 从 ChromaDB 中检索最相似的 top_k 个 chunk
    3. 按距离阈值过滤低质量结果
    4. 返回 SearchResult 列表

    用法::

        retriever = Retriever()
        results = retriever.retrieve("如何实现用户认证？", repo_path="/path/to/repo")

    Args:
        settings: 应用配置；为 ``None`` 时使用默认配置。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化检索器。

        Args:
            settings: 应用配置。
        """
        self._settings = settings or get_settings()
        self._embedder = Embedder.get_instance(self._settings)
        self._store = ChromaStore(self._settings)

    def retrieve(
        self,
        query: str,
        repo_path: str | Path,
        *,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[SearchResult]:
        """执行向量检索。

        Args:
            query: 用户的查询文本。
            repo_path: 仓库路径，用于确定 collection 名称。
            top_k: 返回的最大结果数；为 ``None`` 时使用配置值。
            score_threshold: 距离阈值，超过此值的结果将被过滤；
                为 ``None`` 时使用配置值。

        Returns:
            按相关性排序的 :class:`SearchResult` 列表。

        Raises:
            ValueError: 当仓库对应的 collection 不存在或为空时。
        """
        top_k = top_k if top_k is not None else self._settings.retrieval_top_k
        score_threshold = (
            score_threshold
            if score_threshold is not None
            else self._settings.retrieval_score_threshold
        )

        # 获取 collection 名称
        collection_name = ChromaStore.get_collection_name(repo_path)
        logger.info(
            "开始检索: query='%s', collection='%s', top_k=%d",
            query[:50],
            collection_name,
            top_k,
        )

        # 生成查询向量
        query_embedding = self._embedder.embed_query(query)
        logger.debug("查询向量生成完成 (dim=%d)", len(query_embedding))

        # 执行向量检索
        results = self._store.query(
            collection_name=collection_name,
            embedding=query_embedding,
            top_k=top_k,
            max_distance=score_threshold,
        )

        # top_k 保底回退：如果阈值过滤后无结果，放宽阈值取最相关的若干条
        if not results:
            logger.info(
                "阈值 %.2f 过滤后无结果，回退到 top_%d（不限距离）",
                score_threshold,
                top_k,
            )
            results = self._store.query(
                collection_name=collection_name,
                embedding=query_embedding,
                top_k=top_k,
                max_distance=None,
            )

        logger.info(
            "检索完成: 返回 %d 条结果 (阈值=%.2f)",
            len(results),
            score_threshold,
        )
        return results

    def retrieve_with_context(
        self,
        query: str,
        repo_path: str | Path,
        *,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> RetrievalResult:
        """执行向量检索并组装上下文。

        这是 :meth:`retrieve` 的便捷封装，额外返回格式化后的上下文字符串。

        Args:
            query: 用户的查询文本。
            repo_path: 仓库路径。
            top_k: 返回的最大结果数。
            score_threshold: 距离阈值。

        Returns:
            :class:`RetrievalResult` 实例，包含检索结果和格式化上下文。
        """
        results = self.retrieve(
            query,
            repo_path,
            top_k=top_k,
            score_threshold=score_threshold,
        )

        # 提取 chunks 和 scores
        chunks = [r.chunk for r in results]
        scores = [r.score for r in results]

        # 组装上下文
        context = ContextBuilder.build_context(chunks)

        return RetrievalResult(
            question=query,
            chunks=chunks,
            context=context,
            scores=scores,
        )


# ---------------------------------------------------------------------------
# 上下文组装器
# ---------------------------------------------------------------------------


class ContextBuilder:
    """上下文组装器。

    将检索到的代码切片列表格式化为 LLM 可消费的上下文字符串。
    使用 ``prompts.py`` 中定义的 ``CONTEXT_CHUNK_TEMPLATE`` 模板。

    用法::

        chunks = [chunk1, chunk2, ...]
        context = ContextBuilder.build_context(chunks)
    """

    @staticmethod
    def build_context(chunks: list[CodeChunk], *, max_chunks: int | None = None) -> str:
        """将代码切片列表组装为上下文字符串。

        Args:
            chunks: 代码切片列表，按相关性排序。
            max_chunks: 最大使用的 chunk 数量；为 ``None`` 时使用全部。

        Returns:
            格式化后的上下文字符串，每个 chunk 之间用分隔线分隔。
        """
        if not chunks:
            return ""

        # 限制 chunk 数量
        if max_chunks is not None:
            chunks = chunks[:max_chunks]

        # 格式化每个 chunk
        formatted_chunks: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            try:
                formatted = CONTEXT_CHUNK_TEMPLATE.format(
                    chunk_type=chunk.chunk_type,
                    name=chunk.name,
                    file_path=chunk.file_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    language=chunk.language,
                    content=chunk.source,
                )
                formatted_chunks.append(formatted)
            except (AttributeError, KeyError) as exc:
                logger.warning(
                    "格式化 chunk %d 失败: %s — 跳过",
                    i,
                    exc,
                )
                continue

        if not formatted_chunks:
            return ""

        # 用分隔线连接所有 chunk
        separator = "\n---\n\n"
        context = separator.join(formatted_chunks)

        logger.debug(
            "上下文组装完成: %d 个 chunk, 总长度 %d 字符",
            len(formatted_chunks),
            len(context),
        )
        return context

    @staticmethod
    def build_context_with_summary(
        chunks: list[CodeChunk],
        question: str,
        *,
        max_chunks: int | None = None,
    ) -> str:
        """组装带摘要信息的上下文。

        在上下文开头添加检索摘要，帮助 LLM 理解上下文来源。

        Args:
            chunks: 代码切片列表。
            question: 用户的原始问题。
            max_chunks: 最大使用的 chunk 数量。

        Returns:
            带摘要的格式化上下文字符串。
        """
        if not chunks:
            return ""

        # 构建摘要
        file_paths = list({chunk.file_path for chunk in chunks})
        languages = list({chunk.language for chunk in chunks})

        summary_parts = [
            f"检索到 {len(chunks)} 个相关代码片段",
            f"涉及文件: {', '.join(file_paths[:5])}" + ("..." if len(file_paths) > 5 else ""),
            f"涉及语言: {', '.join(languages)}",
        ]
        summary = "\n".join(summary_parts)

        # 组装主体上下文
        body = ContextBuilder.build_context(chunks, max_chunks=max_chunks)

        return f"{summary}\n\n{body}"
