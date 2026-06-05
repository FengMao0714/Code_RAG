"""检索结果重排序与融合。

实现经典的 **Reciprocal Rank Fusion (RRF)** 算法，
将多个检索通道（向量 / 词法）的结果融合为单一排序。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from code_rag.store.vector_store import SearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 协议：Reranker
# ---------------------------------------------------------------------------


class Reranker(Protocol):
    """重排序器协议。"""

    def rerank(
        self,
        channel_results: dict[str, list[SearchResult]],
        *,
        top_k: int | None = None,
    ) -> list[SearchResult]:
        """融合多通道结果并返回重排后的列表。"""
        ...


# ---------------------------------------------------------------------------
# RRF 重排序
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RRFConfig:
    """RRF 配置。

    Attributes:
        k: 平滑常数，控制高排名 vs 低排名通道的差异。
            标准值为 60，越大融合越平滑。
        weights: 各通道权重（如 ``{"vector": 1.0, "lexical": 0.8}``）。
            缺失的通道使用默认权重 1.0。
    """

    k: int = 60
    weights: dict[str, float] = field(default_factory=dict)


class RRFReranker:
    """Reciprocal Rank Fusion 重排序器。

    RRF 公式::

        score(d) = sum( weight_c * 1 / (k + rank_c(d)) )

    其中 ``rank_c(d)`` 是文档 ``d`` 在通道 ``c`` 中的排名（1-based），
    缺失则不贡献分数。

    Args:
        config: RRF 配置；为 ``None`` 时使用默认 ``k=60``。
    """

    def __init__(self, config: RRFConfig | None = None) -> None:
        """初始化 RRF 重排序器。"""
        self._config = config or RRFConfig()

    @property
    def config(self) -> RRFConfig:
        """返回当前配置。"""
        return self._config

    def rerank(
        self,
        channel_results: dict[str, list[SearchResult]],
        *,
        top_k: int | None = None,
    ) -> list[SearchResult]:
        """融合多通道结果。

        Args:
            channel_results: ``{channel_name: [SearchResult, ...]}`` 映射。
                每个列表应已按通道相关性排序（向量：score 升序；词法：score 降序）。
            top_k: 截取的最终结果数；为 ``None`` 时返回全部。

        Returns:
            按 RRF 分数降序排列的 :class:`SearchResult` 列表。
            ``score`` 字段被覆盖为 RRF 分数（越大越相关）。
        """
        k = self._config.k
        weights = self._config.weights

        # chunk_id -> (rrf_score, best_stage, SearchResult)
        aggregate: dict[str, tuple[float, str, SearchResult]] = {}

        def _key(r: SearchResult) -> str:
            return f"{r.chunk.file_path}:{r.chunk.start_line}:{r.chunk.name}"

        for channel, results in channel_results.items():
            weight = float(weights.get(channel, 1.0))
            for rank, result in enumerate(results, start=1):
                rrf_contrib = weight * 1.0 / (k + rank)
                key = _key(result)
                if key in aggregate:
                    prev_score, prev_stage, prev_result = aggregate[key]
                    new_score = prev_score + rrf_contrib
                    # 保留首次出现的 stage（向量优先）
                    aggregate[key] = (new_score, prev_stage, prev_result)
                else:
                    aggregate[key] = (rrf_contrib, channel, result)

        # 按 RRF 分数降序
        sorted_items = sorted(aggregate.items(), key=lambda kv: kv[1][0], reverse=True)
        final: list[SearchResult] = []
        for _key, (rrf_score, stage, result) in sorted_items:
            # 构造带 stage 信息的新 SearchResult
            new_result = SearchResult(
                chunk=result.chunk,
                score=rrf_score,
            )
            # 通过 setattr 注入 stage 字段（保持 dataclass frozen）
            try:
                object.__setattr__(new_result, "stage", stage)
            except Exception:  # pragma: no cover
                pass
            final.append(new_result)

        if top_k is not None:
            final = final[:top_k]

        logger.info(
            "RRF 融合: %d 通道 -> %d 条结果 (k=%d)",
            len(channel_results),
            len(final),
            k,
        )
        return final


# ---------------------------------------------------------------------------
# 简单辅助：去重 / 截断
# ---------------------------------------------------------------------------


def dedupe_by_chunk(
    results: Iterable[SearchResult],
) -> list[SearchResult]:
    """按 chunk 身份去重，保留首次出现。"""
    seen: set[str] = set()
    out: list[SearchResult] = []
    for r in results:
        key = f"{r.chunk.file_path}:{r.chunk.start_line}:{r.chunk.name}"
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
