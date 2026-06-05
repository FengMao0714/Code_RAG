"""Hybrid 检索器 — 向量召回 + 词法召回 + RRF 融合。

将 :class:`~code_rag.retriever.retriever.Retriever`（向量通道）
与 :class:`~code_rag.retriever.lexical.LexicalRetriever`（词法通道）
的结果用 :class:`~code_rag.retriever.rerank.RRFReranker` 融合。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from code_rag.config import Settings, get_settings
from code_rag.repository import ResolvedRepo
from code_rag.retriever.rerank import RRFReranker
from code_rag.retriever.retriever import Retriever
from code_rag.store.vector_store import SearchResult

logger = logging.getLogger(__name__)

HybridRepo = str | Path | ResolvedRepo


# ---------------------------------------------------------------------------
# 协议
# ---------------------------------------------------------------------------


class _VectorLike(Protocol):
    """向量检索器协议。"""

    def retrieve(
        self,
        query: str,
        repo_path: HybridRepo,
        *,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[SearchResult]: ...


class _LexicalLike(Protocol):
    """词法检索器协议。"""

    def search(
        self,
        query: str,
        *,
        top_k: int = 8,
    ) -> list[SearchResult]: ...


# ---------------------------------------------------------------------------
# Hybrid 结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HybridSearchResult:
    """Hybrid 检索结果。

    Attributes:
        results: 重排后的 :class:`SearchResult` 列表。
        vector_count: 向量通道返回的原始结果数。
        lexical_count: 词法通道返回的原始结果数。
        rrf_top_score: 头部结果的 RRF 分数。
    """

    results: list[SearchResult]
    vector_count: int
    lexical_count: int
    rrf_top_score: float


# ---------------------------------------------------------------------------
# Hybrid 检索器
# ---------------------------------------------------------------------------


class HybridRetriever:
    """向量 + 词法 + RRF 融合检索器。

    用法::

        base = Retriever()
        lex = LexicalRetriever(store, repo_path)
        hybrid = HybridRetriever(base, lex)
        results = hybrid.search("CLI 入口", top_k=8)

    Args:
        vector_retriever: 向量检索器（默认 :class:`Retriever`）。
        lexical_retriever: 词法检索器。
        reranker: 重排序器（默认 :class:`RRFReranker`）。
        vector_top_k: 向量通道召回的候选数。
        lexical_top_k: 词法通道召回的候选数。
        settings: 应用配置。
    """

    def __init__(
        self,
        vector_retriever: _VectorLike | None = None,
        lexical_retriever: _LexicalLike | None = None,
        reranker: RRFReranker | None = None,
        *,
        vector_top_k: int = 50,
        lexical_top_k: int = 50,
        settings: Settings | None = None,
    ) -> None:
        """初始化 Hybrid 检索器。"""
        self._settings = settings or get_settings()
        self._vector = vector_retriever or Retriever(self._settings)
        self._lexical = lexical_retriever
        self._reranker = reranker or RRFReranker()
        self._vector_top_k = vector_top_k
        self._lexical_top_k = lexical_top_k

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        repo_path: HybridRepo,
        *,
        top_k: int = 8,
        score_threshold: float | None = None,
    ) -> list[SearchResult]:
        """执行 hybrid 检索。

        Args:
            query: 用户查询。
            repo_path: 仓库路径。
            top_k: 最终返回数。
            score_threshold: 向量通道距离阈值（可空）。

        Returns:
            按 RRF 分数降序的 :class:`SearchResult` 列表。
        """
        channel_results: dict[str, list[SearchResult]] = {}

        # 向量通道
        try:
            vector_results = self._vector.retrieve(
                query,
                repo_path,
                top_k=self._vector_top_k,
                score_threshold=score_threshold,
            )
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning("向量通道失败: %s", exc)
            vector_results = []
        channel_results["vector"] = vector_results

        # 词法通道
        if self._lexical is not None:
            try:
                lexical_results = self._lexical.search(query, top_k=self._lexical_top_k)
            except Exception as exc:  # pragma: no cover - 防御
                logger.warning("词法通道失败: %s", exc)
                lexical_results = []
            channel_results["lexical"] = lexical_results

        # RRF 融合
        merged = self._reranker.rerank(channel_results, top_k=top_k)

        logger.info(
            "Hybrid 检索: query='%s' vector=%d lexical=%d -> %d",
            query[:50],
            len(channel_results.get("vector", [])),
            len(channel_results.get("lexical", [])),
            len(merged),
        )
        return merged
