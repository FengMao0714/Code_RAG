"""检索评测服务。

编排 golden query 数据集加载、检索、指标计算和报告生成。
不调用 LLM，只评估 retrieval 质量。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_rag.config import Settings, get_settings
from code_rag.evaluation.dataset import GoldenDataset, load_dataset
from code_rag.evaluation.metrics import (
    MetricSummary,
    QueryMetrics,
    compute_metrics,
    compute_query_metrics,
)
from code_rag.evaluation.report import (
    ReportPaths,
    write_json_report,
    write_markdown_report,
)
from code_rag.repository import ResolvedRepo, resolve_repo
from code_rag.retriever.lexical import LexicalRetriever
from code_rag.retriever.retriever import Retriever
from code_rag.store.vector_store import ChromaStore, SearchResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvalOptions:
    """评测可选项。"""

    top_k: int = 8
    mode: str = "vector"
    """vector / lexical / hybrid"""
    output_json: str | None = None
    output_markdown: str | None = None
    repo_path: str = ""


class EvalService:
    """检索评测服务。

    Args:
        settings: 应用配置。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化服务。"""
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def load(self, dataset_path: str | Path) -> GoldenDataset:
        """加载数据集。"""
        return load_dataset(dataset_path)

    def run(
        self,
        dataset: GoldenDataset,
        *,
        repo_path: str | Path,
        top_k: int = 8,
        mode: str = "vector",
        ref: str | None = None,
    ) -> MetricSummary:
        """对数据集执行检索评测。

        Args:
            dataset: 已加载的 golden dataset。
            repo_path: 仓库路径或 git URL。
            top_k: 检索 top_k。
            mode: 检索模式 vector / lexical / hybrid。
            ref: 可选 git ref。

        Returns:
            :class:`MetricSummary`。
        """
        resolved = resolve_repo(str(repo_path), ref=ref, settings=self._settings)
        retrievers: dict[str, Any] = {}
        per_query: list[QueryMetrics] = []
        for query in dataset.queries:
            use_mode = query.mode or mode
            retriever = retrievers.setdefault(
                use_mode,
                self._build_retriever(resolved, use_mode),
            )
            t0 = time.monotonic()
            results = self._retrieve(retriever, query.question, resolved, top_k, use_mode)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            per_query.append(compute_query_metrics(query, results, elapsed_ms=elapsed_ms))
        return compute_metrics(per_query)

    def write_reports(
        self,
        summary: MetricSummary,
        *,
        dataset_name: str,
        repo_path: str,
        top_k: int,
        mode: str,
        output_json: str | None = None,
        output_markdown: str | None = None,
    ) -> ReportPaths:
        """写入 JSON / Markdown 报告。"""
        json_path: Path | None = None
        md_path: Path | None = None
        if output_json:
            json_path = write_json_report(
                summary,
                output_json,
                dataset_name=dataset_name,
                repo_path=repo_path,
                top_k=top_k,
                mode=mode,
            )
        if output_markdown:
            md_path = write_markdown_report(
                summary,
                output_markdown,
                dataset_name=dataset_name,
                repo_path=repo_path,
                top_k=top_k,
                mode=mode,
            )
        return ReportPaths(json_path=json_path, markdown_path=md_path)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _build_retriever(self, resolved: ResolvedRepo, mode: str) -> Any:
        """根据 mode 构造对应的检索器。"""
        if mode == "vector":
            return Retriever(self._settings)
        if mode == "lexical":
            store = ChromaStore(self._settings)
            return LexicalRetriever(store, resolved, self._settings)
        if mode == "hybrid":
            from code_rag.retriever.hybrid import HybridRetriever
            from code_rag.retriever.rerank import RRFReranker

            store = ChromaStore(self._settings)
            return HybridRetriever(
                vector_retriever=Retriever(self._settings),
                lexical_retriever=LexicalRetriever(store, resolved, self._settings),
                reranker=RRFReranker(),
            )
        raise ValueError(f"未知检索模式: {mode}")

    def _retrieve(
        self,
        retriever: Any,
        question: str,
        resolved: ResolvedRepo,
        top_k: int,
        mode: str,
    ) -> list[SearchResult]:
        """调用 retriever。"""
        try:
            if mode == "lexical":
                return retriever.search(question, top_k=top_k)
            if mode == "vector":
                return retriever.retrieve(question, resolved, top_k=top_k)
            if mode == "hybrid":
                return retriever.search(question, resolved, top_k=top_k)
        except Exception as exc:
            logger.warning("检索失败: %s — %s", question[:30], exc)
            return []
        return []
