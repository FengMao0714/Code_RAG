"""Code Agent 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from code_rag.repository import ResolvedRepo


@dataclass(frozen=True)
class AgentTask:
    """单个 Agent 任务。

    Attributes:
        task: 用户任务原文（如"解释登录流程并指出关键文件"）。
        resolved: 已解析的 :class:`ResolvedRepo`。
        plan_only: 是否只生成修改计划（不输出最终回答）。
    """

    task: str
    resolved: ResolvedRepo
    plan_only: bool = True


@dataclass(frozen=True)
class AgentStep:
    """Agent 任务拆解的一个子问题。

    Attributes:
        question: 子问题文本。
        rationale: 为什么需要回答这个子问题。
    """

    question: str
    rationale: str = ""


@dataclass
class AgentPlan:
    """Planner 输出的任务拆解。"""

    steps: list[AgentStep]
    understanding: str = ""
    """对用户任务的理解（中文 / 英文均可）。"""


@dataclass
class AgentEvidence:
    """单个子问题对应的检索证据。

    Attributes:
        step: 关联的 :class:`AgentStep`。
        file_paths: 涉及的文件相对路径（去重）。
        chunk_summaries: 简短的 chunk 摘要，用于 review / 输出。
    """

    step: AgentStep
    file_paths: list[str] = field(default_factory=list)
    chunk_summaries: list[str] = field(default_factory=list)


@dataclass
class AgentReport:
    """Code Agent 最终输出。

    Attributes:
        task: 用户任务原文。
        resolved: 已解析的 :class:`ResolvedRepo`。
        understanding: 任务理解（reasoner 输出）。
        plan: 任务拆解。
        evidence: 每个子问题对应的证据。
        key_files: 关键文件列表（按命中频率 / 重要度排序，去重）。
        suggested_changes: 修改方案（plan-only 模式下也会输出）。
        risks: 风险点列表。
        suggested_tests: 建议运行的测试命令。
        references: 引用到的 chunk 摘要（可读性 + review 用）。
        insufficient_evidence: 是否证据不足（True 时 review 会拒绝臆测）。
        review_note: Reviewer 的备注，例如"证据不足，建议补充信息"。
    """

    task: str
    resolved: ResolvedRepo
    understanding: str
    plan: AgentPlan
    evidence: list[AgentEvidence]
    key_files: list[str] = field(default_factory=list)
    suggested_changes: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    suggested_tests: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    insufficient_evidence: bool = False
    review_note: str = ""

    @property
    def repo_path(self) -> Path:
        """任务对应的 :class:`ResolvedRepo.root_path`。"""
        return self.resolved.root_path
