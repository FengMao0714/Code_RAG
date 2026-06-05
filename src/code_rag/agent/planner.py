"""任务拆解器（Planner）。

Planner 把用户的高级任务（自然语言）拆分为 3~6 个可被检索系统
直接回答的子问题，并为每个子问题补充一段说明（rationale）。

实现原则：

- **离线**：不调用 LLM，纯规则 / 启发式。
- **可解释**：所有步骤都附带 rationale，便于 Agent 报告里展示。
- **轻量**：输入一个任务字符串，输出 :class:`AgentPlan`。
- **幂等**：同一任务多次调用应产生稳定、可复现的拆解。

设计思路：

- 任务文本按标点（句号 / 问号 / 感叹号 / 分号）拆分成句子。
- 拆分后若仍不足 3 条，则补充若干"角度补全"子问题
  （架构、关键文件、风险、测试）。
- 若拆分后超过 6 条，只保留前 6 条。
- understanding 字段由 Planner 自动生成一段简短总结，便于 Reasoner 后续引用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from code_rag.agent.models import AgentPlan, AgentStep

# 角度补全的兜底子问题，确保拆解结果有 3~6 条
_FALLBACK_QUESTIONS: list[tuple[str, str]] = [
    (
        "这个仓库的主要模块结构和入口文件是什么？",
        "先了解整体架构，便于把任务定位到具体模块",
    ),
    (
        "实现这个任务涉及哪些关键文件和函数？",
        "找出实现任务最相关的核心符号，避免大面积改动",
    ),
    (
        "现有代码中是否有可复用的工具函数或类？",
        "避免重复造轮子，优先复用已有抽象",
    ),
    (
        "改动可能影响哪些调用方或上下游？",
        "评估改动面，提早识别回归风险",
    ),
    (
        "如何验证改动的正确性？需要运行哪些测试？",
        "给出可执行的测试 / 验证命令",
    ),
    (
        "是否存在潜在的边界条件或异常路径？",
        "覆盖边界场景，避免遗漏健壮性处理",
    ),
]

# 句子切分正则（中英文常见分隔符）
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;.\n]+")


def _split_sentences(text: str) -> list[str]:
    """把任务文本按标点拆成句子。"""
    raw = _SENTENCE_SPLIT_RE.split(text)
    return [s.strip() for s in raw if s and s.strip()]


def _build_understanding(task: str, step_count: int) -> str:
    """生成对用户任务的简短理解。"""
    short = task.strip()
    if len(short) > 80:
        short = short[:77] + "..."
    return f"用户任务：{short}（拆解为 {step_count} 个子问题）"


@dataclass
class Planner:
    """任务拆解器。

    Attributes:
        min_steps: 最少拆解条数，少于该数时使用兜底补全。
        max_steps: 最多拆解条数，超过时截断。
    """

    min_steps: int = 3
    max_steps: int = 6

    def plan(self, task: str) -> AgentPlan:
        """把任务拆解为子问题。

        Args:
            task: 用户任务原文。

        Returns:
            :class:`AgentPlan`。
        """
        sentences = _split_sentences(task)
        steps: list[AgentStep] = []

        # 把每个有意义的句子直接作为一个子问题
        for sentence in sentences:
            if not sentence:
                continue
            rationale = self._rationale_for_sentence(sentence)
            steps.append(AgentStep(question=sentence, rationale=rationale))

        # 不足最小条数时，用兜底角度补全
        if len(steps) < self.min_steps:
            for question, rationale in _FALLBACK_QUESTIONS:
                if len(steps) >= self.min_steps:
                    break
                if any(s.question == question for s in steps):
                    continue
                steps.append(AgentStep(question=question, rationale=rationale))

        # 超过最大条数时截断
        if len(steps) > self.max_steps:
            steps = steps[: self.max_steps]

        understanding = _build_understanding(task, len(steps))
        return AgentPlan(steps=steps, understanding=understanding)

    @staticmethod
    def _rationale_for_sentence(sentence: str) -> str:
        """为单个句子生成 rationale。"""
        s = sentence.strip()
        if not s:
            return "补充子问题"
        # 简化处理：rationale 直接复用句子首句或兜底说明
        if any(kw in s for kw in ("怎么", "如何", "how", "How", "实现", "如何做")):
            return "理解任务的具体实现路径"
        if any(kw in s for kw in ("为什么", "为何", "why", "Why", "原因")):
            return "解释问题背后的设计 / 决策原因"
        if any(kw in s for kw in ("哪些", "哪里", "哪个", "what", "What", "文件", "函数")):
            return "定位到具体文件 / 符号"
        return "用户任务中的一个独立子目标"
