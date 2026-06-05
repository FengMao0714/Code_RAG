"""检索指标计算。

实现：
- Recall@1 / Recall@3 / Recall@8
- MRR（Mean Reciprocal Rank）
- expected file hit / expected symbol hit（命中率）
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from code_rag.evaluation.dataset import GoldenQuery
from code_rag.store.vector_store import SearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryMetrics:
    """单条 query 的指标。"""

    query_id: str
    question: str
    has_expected_target: bool
    """是否配置了可计分的 expected_files 或 expected_symbols。"""
    has_expected_files: bool
    """是否配置了 expected_files。"""
    has_expected_symbols: bool
    """是否配置了 expected_symbols。"""
    recall_at_1: float
    recall_at_3: float
    recall_at_8: float
    reciprocal_rank: float
    file_hit: bool
    """是否在 top_k 内命中任何 expected_files。"""
    symbol_hit: bool
    """是否在 top_k 内命中任何 expected_symbols。"""
    first_hit_rank: int | None
    """首次命中的排名（1-based），未命中为 None。"""
    hit_file: str | None
    """命中的文件路径。"""
    elapsed_ms: float


@dataclass(frozen=True)
class MetricSummary:
    """聚合指标。"""

    total: int
    recall_at_1: float
    recall_at_3: float
    recall_at_8: float
    mrr: float
    file_hit_rate: float
    symbol_hit_rate: float
    avg_latency_ms: float
    per_query: list[QueryMetrics] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    """统一的路径/符号比较：去除前后空白，转小写，正反斜杠统一。"""
    return s.strip().replace("\\", "/").lower()


def _check_expected(
    result_files: list[str],
    result_names: list[str],
    result_parents: list[str],
    expected_files: Iterable[str],
    expected_symbols: Iterable[str],
) -> tuple[bool, bool, int | None, str | None]:
    """判断一条检索结果列表是否命中 expected。

    Returns:
        ``(file_hit, symbol_hit, first_rank, hit_file)``
    """
    norm_files = [_normalize(f) for f in result_files]
    norm_names = [_normalize(n) for n in result_names]
    norm_parents = [_normalize(p) for p in result_parents]

    expected_files_norm = [_normalize(f) for f in expected_files if f]
    expected_symbols_norm = [_normalize(s) for s in expected_symbols if s]

    file_hit = False
    first_rank: int | None = None
    hit_file: str | None = None

    for rank, (f, n, p) in enumerate(zip(norm_files, norm_names, norm_parents), start=1):
        if expected_files_norm and any(ef in f or f.endswith(ef) for ef in expected_files_norm):
            file_hit = True
            if first_rank is None:
                first_rank = rank
                hit_file = result_files[rank - 1]

        if expected_symbols_norm and any(es in n or es in p for es in expected_symbols_norm):
            if first_rank is None:
                first_rank = rank
                hit_file = result_files[rank - 1]

    symbol_hit = False
    if expected_symbols_norm:
        for n, p in zip(norm_names, norm_parents):
            if any(es in n or es in p for es in expected_symbols_norm):
                symbol_hit = True
                break

    return file_hit, symbol_hit, first_rank, hit_file


# ---------------------------------------------------------------------------
# 单条计算
# ---------------------------------------------------------------------------


def _reciprocal_rank(first_rank: int | None) -> float:
    """首命中排名的倒数。"""
    if first_rank is None or first_rank < 1:
        return 0.0
    return 1.0 / first_rank


def compute_query_metrics(
    query: GoldenQuery,
    results: list[SearchResult],
    *,
    elapsed_ms: float = 0.0,
) -> QueryMetrics:
    """计算单条 query 的指标。

    Args:
        query: golden query。
        results: 检索结果（按相关性排序）。
        elapsed_ms: 检索耗时（毫秒）。

    Returns:
        :class:`QueryMetrics`。
    """
    has_expected_files = bool(query.expected_files)
    has_expected_symbols = bool(query.expected_symbols)
    has_expected_target = has_expected_files or has_expected_symbols

    if not results:
        return QueryMetrics(
            query_id=query.id,
            question=query.question,
            has_expected_target=has_expected_target,
            has_expected_files=has_expected_files,
            has_expected_symbols=has_expected_symbols,
            recall_at_1=0.0,
            recall_at_3=0.0,
            recall_at_8=0.0,
            reciprocal_rank=0.0,
            file_hit=False,
            symbol_hit=False,
            first_hit_rank=None,
            hit_file=None,
            elapsed_ms=elapsed_ms,
        )

    files = [r.chunk.file_path for r in results]
    names = [r.chunk.name for r in results]
    parents = [r.chunk.parent or "" for r in results]

    file_hit, symbol_hit, first_rank, hit_file = _check_expected(
        files, names, parents, query.expected_files, query.expected_symbols
    )

    rr = _reciprocal_rank(first_rank)

    def _recall(k: int) -> float:
        if not has_expected_target:
            return 0.0
        if first_rank is None or first_rank > k:
            return 0.0
        return 1.0

    return QueryMetrics(
        query_id=query.id,
        question=query.question,
        has_expected_target=has_expected_target,
        has_expected_files=has_expected_files,
        has_expected_symbols=has_expected_symbols,
        recall_at_1=_recall(1),
        recall_at_3=_recall(3),
        recall_at_8=_recall(8),
        reciprocal_rank=rr,
        file_hit=file_hit,
        symbol_hit=symbol_hit,
        first_hit_rank=first_rank,
        hit_file=hit_file,
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def mean_reciprocal_rank(per_query: list[QueryMetrics]) -> float:
    """计算 MRR。"""
    if not per_query:
        return 0.0
    return sum(q.reciprocal_rank for q in per_query) / len(per_query)


def compute_metrics(per_query: list[QueryMetrics]) -> MetricSummary:
    """聚合指标。"""
    if not per_query:
        return MetricSummary(
            total=0,
            recall_at_1=0.0,
            recall_at_3=0.0,
            recall_at_8=0.0,
            mrr=0.0,
            file_hit_rate=0.0,
            symbol_hit_rate=0.0,
            avg_latency_ms=0.0,
            per_query=[],
        )

    n = len(per_query)
    scored = [q for q in per_query if q.has_expected_target]
    file_scored = [q for q in per_query if q.has_expected_files]
    symbol_scored = [q for q in per_query if q.has_expected_symbols]

    recall_1 = sum(q.recall_at_1 for q in scored) / len(scored) if scored else 0.0
    recall_3 = sum(q.recall_at_3 for q in scored) / len(scored) if scored else 0.0
    recall_8 = sum(q.recall_at_8 for q in scored) / len(scored) if scored else 0.0
    mrr = mean_reciprocal_rank(scored)
    file_hit = sum(1 for q in file_scored if q.file_hit) / len(file_scored) if file_scored else 0.0
    sym_hit = (
        sum(1 for q in symbol_scored if q.symbol_hit) / len(symbol_scored) if symbol_scored else 0.0
    )
    avg_latency = sum(q.elapsed_ms for q in per_query) / n

    return MetricSummary(
        total=n,
        recall_at_1=recall_1,
        recall_at_3=recall_3,
        recall_at_8=recall_8,
        mrr=mrr,
        file_hit_rate=file_hit,
        symbol_hit_rate=sym_hit,
        avg_latency_ms=avg_latency,
        per_query=list(per_query),
    )
