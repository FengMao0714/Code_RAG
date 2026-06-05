"""轻量 Code Agent 模块。

目标：把 ``code-rag`` 从单纯的 RAG CLI 升级为代码仓库智能助手，
但只做**只读分析**，不会自动修改用户仓库。

主要组件：

- :class:`AgentTask` / :class:`AgentStep` / :class:`AgentReport`:
  任务 / 步骤 / 报告数据模型（见 :mod:`code_rag.agent.models`）。
- :class:`Planner`: 把用户任务拆解为 3~6 个检索子问题。
- :class:`CodeAgent`: 综合 Planner / Retriever / Reasoner / Reviewer，
  输出完整的 :class:`AgentReport`。
"""

from code_rag.agent.code_agent import CodeAgent
from code_rag.agent.models import (
    AgentPlan,
    AgentReport,
    AgentStep,
    AgentTask,
)
from code_rag.agent.planner import Planner

__all__ = [
    "AgentPlan",
    "AgentReport",
    "AgentStep",
    "AgentTask",
    "CodeAgent",
    "Planner",
]
