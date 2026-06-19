"""检索 / 问答流程服务。

把 ``cli.py`` 中 ``ask`` / ``chat`` / ``search`` 命令的业务编排集中到
本模块：初始化 Retriever、调用检索、组装上下文、（可选）调用 LLM。

支持本地路径和 Git 远程仓库（统一通过 :mod:`code_rag.repository` 抽象）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path

from code_rag.config import Settings, get_settings
from code_rag.generator.llm import LLMClient, StreamingChunk
from code_rag.repository import ResolvedRepo, resolve_repo
from code_rag.retriever.hybrid import HybridRetriever
from code_rag.retriever.lexical import LexicalRetriever
from code_rag.retriever.modes import SearchMode
from code_rag.retriever.rerank import RRFReranker
from code_rag.retriever.retriever import ContextBuilder, RetrievalResult, Retriever
from code_rag.store.vector_store import ChromaStore, SearchResult

logger = logging.getLogger(__name__)


# 检索 / 问答时接受的入参：str | Path | ResolvedRepo
QueryKey = str | Path | ResolvedRepo


# 检索函数签名：(query, top_k, score_threshold) -> list[SearchResult]
RetrieverFn = Callable[[str, int | None, float | None], list[SearchResult]]


def build_retriever(
    mode: str,
    settings: Settings,
    resolved: ResolvedRepo,
) -> RetrieverFn:
    """根据检索模式构造统一的检索函数。

    Args:
        mode: 检索模式 — ``vector`` / ``lexical`` / ``hybrid``。
        settings: 应用配置。
        resolved: 已解析的仓库。

    Returns:
        检索函数，签名为 ``(query, top_k, score_threshold) -> list[SearchResult]``。

    Raises:
        ValueError: 不支持的检索模式。
    """
    normalized_mode = SearchMode.normalize(mode)
    if normalized_mode == SearchMode.VECTOR:
        retriever = Retriever(settings)

        def _vector(
            query: str,
            top_k: int | None = None,
            score_threshold: float | None = None,
        ) -> list[SearchResult]:
            return retriever.retrieve(query, resolved, top_k=top_k, score_threshold=score_threshold)

        return _vector

    if normalized_mode == SearchMode.LEXICAL:
        store = ChromaStore(settings)
        lex = LexicalRetriever(store, resolved, settings=settings)

        def _lexical(
            query: str,
            top_k: int | None = None,
            score_threshold: float | None = None,
        ) -> list[SearchResult]:
            return lex.search(query, top_k=top_k or settings.retrieval_top_k)

        return _lexical

    if normalized_mode == SearchMode.HYBRID:
        vector_retriever = Retriever(settings)
        store = ChromaStore(settings)
        lexical_retriever = LexicalRetriever(store, resolved, settings=settings)
        hybrid = HybridRetriever(
            vector_retriever=vector_retriever,
            lexical_retriever=lexical_retriever,
            reranker=RRFReranker(),
            settings=settings,
        )

        def _hybrid(
            query: str,
            top_k: int | None = None,
            score_threshold: float | None = None,
        ) -> list[SearchResult]:
            return hybrid.search(
                query,
                resolved,
                top_k=top_k or settings.retrieval_top_k,
                score_threshold=score_threshold,
            )

        return _hybrid

    raise ValueError(f"不支持的检索模式: {mode}（应为 vector/lexical/hybrid）")


@dataclass(frozen=True)
class QueryResult:
    """单次问答的最终结果。"""

    question: str
    retrieval: RetrievalResult
    """底层检索结果（包含 chunks / context / scores）。"""
    low_confidence: bool
    """是否被判定为低置信度（context 为空或全部为 doc chunk）。"""
    reason: str
    """低置信度原因描述，为空字符串表示置信度充足。"""


class QueryService:
    """检索与问答流程服务。

    Args:
        settings: 应用配置；为 ``None`` 时使用默认配置。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化服务。"""
        self._settings = settings or get_settings()
        self._retriever: Retriever | None = None
        self._llm: LLMClient | None = None

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        repo: QueryKey,
        *,
        ref: str | None = None,
        top_k: int | None = None,
        score_threshold: float | None = None,
        mode: str = "hybrid",
    ) -> list[SearchResult]:
        """只执行检索，不调用 LLM。

        Args:
            query: 查询文本。
            repo: 仓库标识。
            ref: 可选 git ref。
            top_k: 返回结果数。
            score_threshold: 距离阈值。
            mode: 检索模式 — ``vector`` / ``lexical`` / ``hybrid``。
        """
        resolved = self._ensure_resolved(repo, ref=ref)
        retriever_fn = build_retriever(mode, self._settings, resolved)
        return retriever_fn(query, top_k, score_threshold)

    def ask(
        self,
        question: str,
        repo: QueryKey,
        *,
        ref: str | None = None,
        top_k: int | None = None,
        score_threshold: float | None = None,
        mode: str = "hybrid",
    ) -> QueryResult:
        """检索 + 低置信度评估。

        Args:
            question: 用户问题。
            repo: 仓库标识（本地路径、git URL 或 :class:`ResolvedRepo`）。
            ref: 可选 git ref，仅当 ``repo`` 不是 :class:`ResolvedRepo` 时生效。
            top_k: 检索 top_k。
            score_threshold: 距离阈值。
            mode: 检索模式 — ``vector`` / ``lexical`` / ``hybrid``。

        Returns:
            :class:`QueryResult`，包含 retrieval 与 low_confidence 标记。
        """
        results = self.search(
            question,
            repo,
            ref=ref,
            top_k=top_k,
            score_threshold=score_threshold,
            mode=mode,
        )
        chunks = [r.chunk for r in results]
        scores = [r.score for r in results]
        context = ContextBuilder.build_context(chunks, scores=scores)
        retrieval = RetrievalResult(
            question=question,
            chunks=chunks,
            context=context,
            scores=scores,
        )
        low, reason = self._evaluate_confidence(retrieval, mode=mode)
        return QueryResult(
            question=question,
            retrieval=retrieval,
            low_confidence=low,
            reason=reason,
        )

    def stream_answer(
        self,
        question: str,
        repo: QueryKey,
        *,
        ref: str | None = None,
        top_k: int | None = None,
        score_threshold: float | None = None,
        mode: str = "hybrid",
    ) -> tuple[QueryResult, Generator[StreamingChunk, None, None]]:
        """检索 + 流式生成 LLM 回答。"""
        result = self.ask(
            question,
            repo,
            ref=ref,
            top_k=top_k,
            score_threshold=score_threshold,
            mode=mode,
        )
        llm = self._get_llm()
        return result, llm.generate_stream(result.retrieval.context, question)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get_llm(self) -> LLMClient:
        """懒加载 LLM 客户端。"""
        if self._llm is None:
            self._llm = LLMClient(self._settings)
        return self._llm

    def _ensure_resolved(self, repo: QueryKey, *, ref: str | None = None) -> ResolvedRepo:
        """把入参规范化为 :class:`ResolvedRepo`。"""
        if isinstance(repo, ResolvedRepo):
            return repo
        return resolve_repo(str(repo), ref=ref, settings=self._settings)

    @staticmethod
    def _evaluate_confidence(
        retrieval: RetrievalResult,
        *,
        mode: str = "vector",
    ) -> tuple[bool, str]:
        """根据检索结果评估置信度。

        判定规则：

        - 上下文为空 → 低置信度（无证据）
        - 所有 chunk 都属于 doc chunk（README/CONFIG）→ 低置信度（缺少代码证据）
        - vector 模式：平均距离 > 1.0 → 低置信度（结果可能不相关）
        - hybrid/lexical 模式：RRF/词法分数越大越好，不做距离检查

        Args:
            retrieval: 检索结果。
            mode: 检索模式（``vector`` / ``hybrid`` / ``lexical``）。

        Returns:
            ``(low_confidence, reason)``。
        """
        if not retrieval.chunks:
            return True, "未找到任何相关代码片段"

        code_chunks = [c for c in retrieval.chunks if c.chunk_type != "doc"]
        if not code_chunks:
            return True, "命中全部为文档/配置文件，未检索到代码片段"

        # 仅 vector 模式检查距离阈值（距离越小越相关）
        if mode == "vector" and retrieval.scores:
            avg_score = sum(retrieval.scores) / len(retrieval.scores)
            if avg_score > 1.0:
                return True, f"检索平均距离过高 ({avg_score:.2f})，结果可能不相关"

        return False, ""

    # 显式暴露类型，便于 IDE 提示
    _llm: LLMClient | None
    _retriever: Retriever | None
