"""检索评测模块。

提供 golden query 数据集加载、检索指标计算和报告生成能力。
``eval`` CLI 命令通过本模块评估 retrieval 的质量。
"""

from code_rag.evaluation.dataset import GoldenDataset, GoldenQuery, load_dataset
from code_rag.evaluation.metrics import (
    MetricSummary,
    QueryMetrics,
    compute_metrics,
    mean_reciprocal_rank,
)
from code_rag.evaluation.report import (
    ReportPaths,
    render_markdown,
    write_json_report,
    write_markdown_report,
)

__all__ = [
    "GoldenDataset",
    "GoldenQuery",
    "load_dataset",
    "MetricSummary",
    "QueryMetrics",
    "compute_metrics",
    "mean_reciprocal_rank",
    "ReportPaths",
    "render_markdown",
    "write_json_report",
    "write_markdown_report",
]
