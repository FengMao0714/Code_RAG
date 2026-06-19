"""评测报告生成（JSON + Markdown）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from code_rag.evaluation.metrics import MetricSummary, QueryMetrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportPaths:
    """JSON / Markdown 报告的输出路径。"""

    json_path: Path | None = None
    markdown_path: Path | None = None


def _summary_to_dict(summary: MetricSummary) -> dict[str, Any]:
    """指标摘要序列化为字典。"""
    return {
        "total": summary.total,
        "recall_at_1": round(summary.recall_at_1, 4),
        "recall_at_3": round(summary.recall_at_3, 4),
        "recall_at_8": round(summary.recall_at_8, 4),
        "mrr": round(summary.mrr, 4),
        "file_hit_rate": round(summary.file_hit_rate, 4),
        "symbol_hit_rate": round(summary.symbol_hit_rate, 4),
        "avg_latency_ms": round(summary.avg_latency_ms, 2),
    }


def _query_to_dict(q: QueryMetrics) -> dict[str, Any]:
    """单条 query 指标序列化为字典。"""
    return {
        "id": q.query_id,
        "question": q.question,
        "recall_at_1": round(q.recall_at_1, 4),
        "recall_at_3": round(q.recall_at_3, 4),
        "recall_at_8": round(q.recall_at_8, 4),
        "reciprocal_rank": round(q.reciprocal_rank, 4),
        "file_hit": q.file_hit,
        "symbol_hit": q.symbol_hit,
        "first_hit_rank": q.first_hit_rank,
        "hit_file": q.hit_file,
        "elapsed_ms": round(q.elapsed_ms, 2),
    }


def write_json_report(
    summary: MetricSummary,
    output_path: str | Path,
    *,
    dataset_name: str = "",
    repo_path: str = "",
    top_k: int = 8,
    mode: str = "vector",
) -> Path:
    """将评测结果写入 JSON 报告。

    Args:
        summary: 指标摘要。
        output_path: 输出 JSON 路径。
        dataset_name: 数据集名称。
        repo_path: 评测仓库路径。
        top_k: 检索 top_k。
        mode: 检索模式。

    Returns:
        实际写入路径。
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": dataset_name,
        "repo_path": repo_path,
        "mode": mode,
        "top_k": top_k,
        "summary": _summary_to_dict(summary),
        "per_query": [_query_to_dict(q) for q in summary.per_query],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("已写入 JSON 报告: %s", path)
    return path


def render_markdown(
    summary: MetricSummary,
    *,
    dataset_name: str = "",
    repo_path: str = "",
    top_k: int = 8,
    mode: str = "vector",
) -> str:
    """渲染 Markdown 报告。"""
    lines: list[str] = []
    lines.append(f"# Retrieval Eval Report — {dataset_name or 'unknown'}")
    lines.append("")
    lines.append(f"- Generated at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Repository: `{repo_path}`")
    lines.append(f"- Mode: `{mode}`")
    lines.append(f"- top_k: {top_k}")
    lines.append(f"- Total queries: {summary.total}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Recall@1 | {summary.recall_at_1:.2%} |")
    lines.append(f"| Recall@3 | {summary.recall_at_3:.2%} |")
    lines.append(f"| Recall@8 | {summary.recall_at_8:.2%} |")
    lines.append(f"| MRR | {summary.mrr:.4f} |")
    lines.append(f"| File hit rate | {summary.file_hit_rate:.2%} |")
    lines.append(f"| Symbol hit rate | {summary.symbol_hit_rate:.2%} |")
    lines.append(f"| Avg latency (ms) | {summary.avg_latency_ms:.2f} |")
    lines.append("")
    lines.append("## Per-Query Results")
    lines.append("")
    lines.append("| ID | Question | R@1 | R@3 | R@8 | MRR | First Rank | File | Latency (ms) |")
    lines.append("|----|----------|-----|-----|-----|-----|------------|------|--------------|")
    for q in summary.per_query:
        first_rank = q.first_hit_rank if q.first_hit_rank is not None else "-"
        hit_file = q.hit_file or "-"
        lines.append(
            f"| {q.query_id} | {q.question} | "
            f"{q.recall_at_1:.0f} | {q.recall_at_3:.0f} | {q.recall_at_8:.0f} | "
            f"{q.reciprocal_rank:.2f} | {first_rank} | {hit_file} | "
            f"{q.elapsed_ms:.0f} |"
        )
    return "\n".join(lines) + "\n"


def write_markdown_report(
    summary: MetricSummary,
    output_path: str | Path,
    *,
    dataset_name: str = "",
    repo_path: str = "",
    top_k: int = 8,
    mode: str = "vector",
) -> Path:
    """将评测结果写入 Markdown 报告。"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    md = render_markdown(
        summary,
        dataset_name=dataset_name,
        repo_path=repo_path,
        top_k=top_k,
        mode=mode,
    )
    path.write_text(md, encoding="utf-8")
    logger.info("已写入 Markdown 报告: %s", path)
    return path


def write_comparison_json_report(
    comparison: dict[str, MetricSummary],
    output_path: str | Path,
    *,
    dataset_name: str = "",
    repo_path: str = "",
    top_k: int = 8,
) -> Path:
    """Write a JSON report comparing multiple retrieval modes."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": dataset_name,
        "repo_path": repo_path,
        "top_k": top_k,
        "modes": {
            mode: {
                "summary": _summary_to_dict(summary),
                "per_query": [_query_to_dict(q) for q in summary.per_query],
            }
            for mode, summary in comparison.items()
        },
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("已写入对比 JSON 报告: %s", path)
    return path


def render_comparison_markdown(
    comparison: dict[str, MetricSummary],
    *,
    dataset_name: str = "",
    repo_path: str = "",
    top_k: int = 8,
) -> str:
    """Render a Markdown report comparing retrieval modes."""
    lines: list[str] = []
    lines.append(f"# Retrieval Eval Comparison — {dataset_name or 'unknown'}")
    lines.append("")
    lines.append(f"- Generated at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Repository: `{repo_path}`")
    lines.append(f"- top_k: {top_k}")
    lines.append("")
    lines.append("## Mode Summary")
    lines.append("")
    lines.append(
        "| Mode | Recall@1 | Recall@3 | Recall@8 | MRR | File Hit | Symbol Hit | Avg Latency |"
    )
    lines.append(
        "|------|----------|----------|----------|-----|----------|------------|-------------|"
    )
    for mode, summary in comparison.items():
        lines.append(
            f"| {mode} | {summary.recall_at_1:.2%} | {summary.recall_at_3:.2%} | "
            f"{summary.recall_at_8:.2%} | {summary.mrr:.4f} | "
            f"{summary.file_hit_rate:.2%} | {summary.symbol_hit_rate:.2%} | "
            f"{summary.avg_latency_ms:.2f}ms |"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "Hybrid retrieval should be evaluated against vector and lexical baselines before "
        "claiming ranking improvements."
    )
    return "\n".join(lines) + "\n"


def write_comparison_markdown_report(
    comparison: dict[str, MetricSummary],
    output_path: str | Path,
    *,
    dataset_name: str = "",
    repo_path: str = "",
    top_k: int = 8,
) -> Path:
    """Write a Markdown report comparing retrieval modes."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_comparison_markdown(
            comparison,
            dataset_name=dataset_name,
            repo_path=repo_path,
            top_k=top_k,
        ),
        encoding="utf-8",
    )
    logger.info("已写入对比 Markdown 报告: %s", path)
    return path
