"""Code Agent 单元 / 集成测试。

覆盖：

- :class:`Planner` 的任务拆解（3~6 条、含 rationale）
- :class:`CodeAgent.run` 的端到端流程（plan → 检索 → 汇总）
- ``agent`` CLI 命令的 ``--plan-only`` 输出结构
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from code_rag.agent import AgentTask, CodeAgent, Planner
from code_rag.cli import app
from code_rag.repository import resolve_repo

runner = CliRunner()


def _make_repo(tmp_path: Path) -> Path:
    """创建一个最小可索引的本地仓库。"""
    repo = tmp_path / "demo_repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "main.py").write_text(
        "def login(user, pwd):\n    return True\n\ndef logout():\n    return False\n",
        encoding="utf-8",
    )
    (repo / "utils.py").write_text(
        "def hash_pwd(pwd: str) -> str:\n    return pwd\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Demo\n\nDemonstrates login flow.\n", encoding="utf-8")
    return repo


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class TestPlanner:
    def test_simple_task_splits_sentences(self) -> None:
        planner = Planner()
        plan = planner.plan("解释登录流程。找出关键文件。如何测试？")
        assert 3 <= len(plan.steps) <= 6
        # 3 个原始句子应被保留
        questions = [s.question for s in plan.steps]
        assert any("登录流程" in q for q in questions)
        assert any("关键文件" in q for q in questions)
        assert any("测试" in q for q in questions)
        # rationale 不应为空
        assert all(s.rationale for s in plan.steps)

    def test_short_task_falls_back_to_angle_questions(self) -> None:
        planner = Planner()
        plan = planner.plan("优化")
        # 1 个原句不足 3 条 → 应补全到 3 条
        assert len(plan.steps) >= 3
        assert all(s.rationale for s in plan.steps)

    def test_too_many_sentences_truncated(self) -> None:
        planner = Planner()
        plan = planner.plan("第一步。第二步。第三步。第四步。第五步。第七步。第八步。")
        assert len(plan.steps) <= planner.max_steps
        assert len(plan.steps) >= planner.min_steps

    def test_understanding_summary_present(self) -> None:
        planner = Planner()
        plan = planner.plan("解释用户认证的实现方式")
        assert plan.understanding
        assert "用户任务" in plan.understanding


# ---------------------------------------------------------------------------
# Code Agent
# ---------------------------------------------------------------------------


class TestCodeAgent:
    def test_run_returns_report(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        repo = _make_repo(tmp_path)
        # 先建立索引，使后续检索有数据
        from code_rag.services import IndexService

        IndexService(tmp_settings).run_index(repo)

        resolved = resolve_repo(str(repo), settings=tmp_settings)
        task = AgentTask(task="解释登录流程并指出关键文件", resolved=resolved, plan_only=True)
        agent_obj = CodeAgent(settings=tmp_settings)
        report = agent_obj.run(task)

        # 基本结构
        assert report.task == "解释登录流程并指出关键文件"
        assert report.resolved is resolved
        assert 3 <= len(report.plan.steps) <= 6
        assert report.plan.understanding
        assert len(report.evidence) == len(report.plan.steps)

        # 每个 step 都应有对应的 evidence
        for ev in report.evidence:
            assert ev.step in report.plan.steps

        # 至少有 1 个文件被识别为关键文件（login / main.py / utils.py 等）
        assert report.key_files, "key_files 不应为空"

    def test_insufficient_evidence_when_no_index(self, tmp_path: Path, tmp_settings) -> None:
        # 没有索引，retriever 抛 ValueError 时 CodeAgent 应降级
        # 注入空 stub 让 _collect_evidence 不会真的去查 chroma
        repo = _make_repo(tmp_path)
        resolved = resolve_repo(str(repo), settings=tmp_settings)

        agent_obj = CodeAgent(settings=tmp_settings)

        # 替换内部的 _build_hybrid，让 hybrid.search 返回空 list
        class _StubHybrid:
            def search(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
                return []

        with patch.object(CodeAgent, "_build_hybrid", lambda self, resolved: _StubHybrid()):
            task = AgentTask(task="任意任务", resolved=resolved, plan_only=True)
            report = agent_obj.run(task)

        assert report.insufficient_evidence is True
        assert "证据不足" in report.review_note
        assert any("证据不足" in r for r in report.risks)

    def test_report_repo_path_property(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        repo = _make_repo(tmp_path)
        from code_rag.services import IndexService

        IndexService(tmp_settings).run_index(repo)

        resolved = resolve_repo(str(repo), settings=tmp_settings)
        task = AgentTask(task="检查结构", resolved=resolved, plan_only=True)
        agent_obj = CodeAgent(settings=tmp_settings)
        report = agent_obj.run(task)
        # repo_path 应等于 resolved.root_path
        assert report.repo_path == resolved.root_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestAgentCli:
    def test_agent_plan_only_renders_sections(
        self, tmp_path: Path, tmp_settings, patch_embedder
    ) -> None:
        repo = _make_repo(tmp_path)
        from code_rag.services import IndexService

        IndexService(tmp_settings).run_index(repo)

        with patch("code_rag.cli.get_settings", return_value=tmp_settings):
            result = runner.invoke(app, ["agent", str(repo), "解释登录流程", "--plan-only"])
        assert result.exit_code == 0, result.output
        # 报告的多个标题
        assert "任务理解" in result.output
        assert "计划拆解" in result.output
        assert "关键文件" in result.output
        assert "修改建议" in result.output
        assert "建议运行的测试" in result.output

    def test_agent_command_with_git_url_resolves_repo(self, tmp_path: Path, tmp_settings) -> None:
        # 即使仓库不存在 / 不可达，agent 命令应至少能完成 Planner + 输出
        # 这里用一个非 git 路径（_ensure_resolved 会失败），但只验证 CLI 不崩溃
        # 在主流程上 AgentTask 会走 resolve_repo，所以此处用本地路径快速验证 CLI 集成
        repo = _make_repo(tmp_path)
        with patch("code_rag.cli.get_settings", return_value=tmp_settings):
            result = runner.invoke(app, ["agent", str(repo), "如何测试登录？", "--no-plan-only"])
        # 不论 plan_only 与否，输出应至少包含 "任务理解"
        # --no-plan-only 仍然不会真正调用 LLM（CodeAgent.run 是离线的）
        assert "任务理解" in result.output
