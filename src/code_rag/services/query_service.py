"""检索 / 问答流程服务。

把 ``cli.py`` 中 ``ask`` / ``chat`` / ``search`` 命令的业务编排集中到
本模块：初始化 Retriever、调用检索、组装上下文、（可选）调用 LLM。

支持本地路径和 Git 远程仓库（统一通过 :mod:`code_rag.repository` 抽象）。
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

from code_rag.config import Settings, get_settings
from code_rag.generator.llm import LLMClient, StreamingChunk
from code_rag.repository import ResolvedRepo, resolve_repo
from code_rag.retriever.retriever import RetrievalResult, Retriever
from code_rag.store.vector_store import SearchResult

logger = logging.getLogger(__name__)


# 检索 / 问答时接受的入参：str | Path | ResolvedRepo
QueryKey = str | Path | ResolvedRepo


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
    ) -> list[SearchResult]:
        """只执行检索，不调用 LLM。"""
        retriever = self._get_retriever()
        resolved = self._ensure_resolved(repo, ref=ref)
        return retriever.retrieve(
            query,
            resolved,
            top_k=top_k,
            score_threshold=score_threshold,
        )

    def ask(
        self,
        question: str,
        repo: QueryKey,
        *,
        ref: str | None = None,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> QueryResult:
        """检索 + 低置信度评估。

        Args:
            question: 用户问题。
            repo: 仓库标识（本地路径、git URL 或 :class:`ResolvedRepo`）。
            ref: 可选 git ref，仅当 ``repo`` 不是 :class:`ResolvedRepo` 时生效。
            top_k: 检索 top_k。
            score_threshold: 距离阈值。

        Returns:
            :class:`QueryResult`，包含 retrieval 与 low_confidence 标记。
        """
        retriever = self._get_retriever()
        resolved = self._ensure_resolved(repo, ref=ref)
        retrieval = retriever.retrieve_with_context(
            question,
            resolved,
            top_k=top_k,
            score_threshold=score_threshold,
        )
        low, reason = self._evaluate_confidence(retrieval)
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
    ) -> tuple[QueryResult, Generator[StreamingChunk, None, None]]:
        """检索 + 流式生成 LLM 回答。"""
        result = self.ask(
            question,
            repo,
            ref=ref,
            top_k=top_k,
            score_threshold=score_threshold,
        )
        llm = self._get_llm()
        return result, llm.generate_stream(result.retrieval.context, question)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get_retriever(self) -> Retriever:
        """懒加载 Retriever。"""
        if self._retriever is None:
            self._retriever = Retriever(self._settings)
        return self._retriever

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
    def _evaluate_confidence(retrieval: RetrievalResult) -> tuple[bool, str]:
        """根据检索结果评估置信度。

        判定规则：

        - 上下文为空 → 低置信度（无证据）
        - 所有 chunk 都属于 doc chunk（README/CONFIG）→ 低置信度（缺少代码证据）
        - 平均距离 > 1.0 → 低置信度（结果可能不相关）

        Args:
            retrieval: 检索结果。

        Returns:
            ``(low_confidence, reason)``。
        """
        if not retrieval.chunks:
            return True, "未找到任何相关代码片段"

        code_chunks = [c for c in retrieval.chunks if c.chunk_type != "doc"]
        if not code_chunks:
            return True, "命中全部为文档/配置文件，未检索到代码片段"

        # 平均距离（越小越相关），距离过高也视为低置信度
        if retrieval.scores:
            avg_score = sum(retrieval.scores) / len(retrieval.scores)
            if avg_score > 1.0:
                return True, f"检索平均距离过高 ({avg_score:.2f})，结果可能不相关"

        return False, ""

    # 显式暴露类型，便于 IDE 提示
    _llm: LLMClient | None
    _retriever: Retriever | None
