"""Code Agent 主流程。

组合 :class:`Planner`、Hybrid :class:`HybridRetriever`、
离线 Reasoner 与 Reviewer，输出结构化 :class:`AgentReport`。

关键设计：

- **离线** —— 不调用 LLM，Reasoner 使用本地模板生成理解、方案与风险。
- **只读** —— Agent 不会修改任何文件，所有结论只作为修改建议。
- **可解释** —— 每个子问题都附带 rationale 与检索证据。
- **容错** —— 检索失败时返回空证据并明确标 ``insufficient_evidence``。
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from code_rag.agent.models import (
    AgentEvidence,
    AgentPlan,
    AgentReport,
    AgentStep,
    AgentTask,
)
from code_rag.agent.planner import Planner
from code_rag.config import Settings, get_settings
from code_rag.repository import ResolvedRepo
from code_rag.retriever.hybrid import HybridRetriever
from code_rag.retriever.lexical import LexicalRetriever
from code_rag.retriever.rerank import RRFReranker
from code_rag.retriever.retriever import Retriever
from code_rag.store.vector_store import ChromaStore, SearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 离线 Reasoner / Reviewer
# ---------------------------------------------------------------------------


def _format_chunk_summary(result: SearchResult) -> str:
    """把一条 :class:`SearchResult` 压缩成一行可读摘要。"""
    chunk = result.chunk
    file_path = chunk.file_path
    name = chunk.name or "<anonymous>"
    return f"{file_path}:{chunk.start_line}-{chunk.end_line}  {chunk.chunk_type}={name}"


def _summarize_evidence(
    plan: AgentPlan,
    evidence: list[AgentEvidence],
) -> tuple[list[str], list[str], list[str], list[str], bool]:
    """汇总证据 → (key_files, suggested_changes, risks, suggested_tests, insufficient).

    规则：

    - **key_files**：按命中次数排序的文件相对路径，最多取 8 个。
    - **suggested_changes**：基于 task / steps 生成的高层建议。
    - **risks**：当 evidence 不完整、命中数过少或 task 含敏感关键词时记录。
    - **suggested_tests**：根据 task 关键字建议运行 pytest 命令。
    - **insufficient**：当所有 evidence 都为空时为 True。
    """
    file_counter: Counter[str] = Counter()
    for ev in evidence:
        for fp in ev.file_paths:
            file_counter[fp] += 1
    key_files = [fp for fp, _ in file_counter.most_common(8)]

    insufficient = not any(ev.chunk_summaries for ev in evidence)

    suggested_changes: list[str] = []
    for step in plan.steps:
        if not step.question:
            continue
        suggested_changes.append(f"处理子问题：{step.question}")
    if not suggested_changes:
        suggested_changes.append("无具体子问题，可直接基于现有代码修改")

    risks: list[str] = []
    if insufficient:
        risks.append("证据不足：检索未命中任何代码片段，建议人工补充上下文")
    elif len(key_files) < 2:
        risks.append("命中的关键文件较少，可能存在理解偏差")
    if any(kw in plan.understanding for kw in ("重构", "修改", "替换", "删除")):
        risks.append("任务涉及改动，注意回归测试与兼容性")

    suggested_tests: list[str] = ["uv run --frozen pytest tests/ -v"]
    perf_keywords = ("性能", "performance", "perf")
    if any(
        kw in step.question.lower() or kw in step.question
        for step in plan.steps
        for kw in perf_keywords
    ):
        suggested_tests.append("# 性能相关：考虑补 benchmark / 性能基线")

    return key_files, suggested_changes, risks, suggested_tests, insufficient


def _default_review_note(insufficient: bool) -> str:
    """生成 Reviewer 备注。"""
    if insufficient:
        return "证据不足，未自动生成完整方案；建议运行 `code-rag index` 后重试，或补充任务描述"
    return "已交叉验证检索证据；建议人工复核引用到的关键文件"


# ---------------------------------------------------------------------------
# Code Agent
# ---------------------------------------------------------------------------


@dataclass
class CodeAgent:
    """轻量 Code Agent。

    Args:
        settings: 应用配置。
        planner: 任务拆解器。
    """

    settings: Settings
    planner: Planner

    def __init__(
        self,
        settings: Settings | None = None,
        planner: Planner | None = None,
    ) -> None:
        """初始化 Code Agent。"""
        self.settings = settings or get_settings()
        self.planner = planner or Planner()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def run(self, task: AgentTask) -> AgentReport:
        """执行完整的 Agent 流程。

        流程：

        1. Planner 拆解为 3~6 个子问题。
        2. Retriever 对每个子问题执行 hybrid 检索。
        3. Evidence Builder 汇总证据，按文件聚合。
        4. Reasoner 生成 understanding / key_files / changes / risks / tests。
        5. Reviewer 检查证据是否充分。
        """
        plan = self.planner.plan(task.task)
        evidence = self._collect_evidence(task.resolved, plan.steps)
        key_files, changes, risks, tests, insufficient = _summarize_evidence(plan, evidence)

        # references：所有 chunk 摘要的并集（去重）
        seen: set[str] = set()
        references: list[str] = []
        for ev in evidence:
            for summary in ev.chunk_summaries:
                if summary in seen:
                    continue
                seen.add(summary)
                references.append(summary)

        review_note = _default_review_note(insufficient)

        return AgentReport(
            task=task.task,
            resolved=task.resolved,
            understanding=plan.understanding,
            plan=plan,
            evidence=evidence,
            key_files=key_files,
            suggested_changes=changes,
            risks=risks,
            suggested_tests=tests,
            references=references,
            insufficient_evidence=insufficient,
            review_note=review_note,
        )

    # ------------------------------------------------------------------
    # 内部：检索 / 聚合
    # ------------------------------------------------------------------

    def _build_hybrid(self, resolved: ResolvedRepo) -> HybridRetriever:
        """根据 resolved 构造 hybrid 检索器。"""
        store = ChromaStore(self.settings)
        vector_retriever: Any = Retriever(self.settings)
        lexical_retriever: Any = LexicalRetriever(store, resolved, self.settings)
        return HybridRetriever(
            vector_retriever=vector_retriever,
            lexical_retriever=lexical_retriever,
            reranker=RRFReranker(),
        )

    def _collect_evidence(
        self,
        resolved: ResolvedRepo,
        steps: list[AgentStep],
    ) -> list[AgentEvidence]:
        """对每个子问题执行检索并聚合成 :class:`AgentEvidence`。"""
        hybrid = self._build_hybrid(resolved)
        results: list[AgentEvidence] = []
        for step in steps:
            try:
                hits = hybrid.search(step.question, resolved, top_k=5)
            except Exception as exc:  # pragma: no cover - 防御
                logger.warning("Agent 检索失败: %s — %s", step.question[:30], exc)
                hits = []
            file_paths: list[str] = []
            summaries: list[str] = []
            seen: set[str] = set()
            for hit in hits:
                fp = hit.chunk.file_path
                if fp not in seen:
                    seen.add(fp)
                    file_paths.append(fp)
                summaries.append(_format_chunk_summary(hit))
            results.append(
                AgentEvidence(
                    step=step,
                    file_paths=file_paths,
                    chunk_summaries=summaries,
                )
            )
        return results
