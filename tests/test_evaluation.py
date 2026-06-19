"""评测模块测试 — 指标计算、报告生成、golden dataset 加载。"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from code_rag.evaluation.dataset import GoldenDataset, GoldenQuery, load_dataset
from code_rag.evaluation.metrics import (
    compute_metrics,
    compute_query_metrics,
    mean_reciprocal_rank,
)
from code_rag.evaluation.report import (
    render_comparison_markdown,
    render_markdown,
    write_comparison_json_report,
    write_comparison_markdown_report,
    write_json_report,
    write_markdown_report,
)
from code_rag.indexer.chunker import CodeChunk
from code_rag.store.vector_store import SearchResult

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_result(file_path: str, name: str, parent: str | None = None) -> SearchResult:
    chunk = CodeChunk(
        file_path=file_path,
        language="python",
        chunk_type="function",
        name=name,
        start_line=1,
        end_line=1,
        parent=parent,
        file_hash="h",
        source="x",
        token_count=0,
    )
    return SearchResult(chunk=chunk, score=0.1)


def _make_golden(tmp_path: Path) -> Path:
    p = tmp_path / "golden.yaml"
    p.write_text(
        textwrap.dedent(
            """
            - id: q1
              question: CLI 入口在哪里？
              expected_files:
                - src/code_rag/cli.py
              expected_symbols:
                - app
              category: symbol_location
              difficulty: easy
            - id: q2
              question: scanner 如何过滤文件？
              expected_files:
                - src/code_rag/indexer/scanner.py
              expected_symbols:
                - RepoScanner
            """
        ).strip(),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# dataset.py
# ---------------------------------------------------------------------------


class TestDataset:
    def test_load(self, tmp_path: Path) -> None:
        path = _make_golden(tmp_path)
        ds = load_dataset(path)
        assert ds.name == "golden"
        assert len(ds) == 2
        assert isinstance(ds.queries[0], GoldenQuery)
        assert ds.queries[0].id == "q1"
        assert any("cli.py" in f for f in ds.queries[0].expected_files)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_dataset(tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------
# metrics.py
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_compute_query_hit(self) -> None:
        q = GoldenQuery(
            id="q1",
            question="q",
            expected_files=["src/cli.py"],
            expected_symbols=["app"],
        )
        results = [_make_result("src/cli.py", "app")]
        m = compute_query_metrics(q, results)
        assert m.file_hit is True
        assert m.symbol_hit is True
        assert m.recall_at_1 == 1.0
        assert m.reciprocal_rank == 1.0
        assert m.first_hit_rank == 1

    def test_compute_query_miss(self) -> None:
        q = GoldenQuery(
            id="q1", question="q", expected_files=["src/cli.py"], expected_symbols=["app"]
        )
        results = [_make_result("src/other.py", "Foo")]
        m = compute_query_metrics(q, results)
        assert m.file_hit is False
        assert m.symbol_hit is False
        assert m.recall_at_1 == 0.0
        assert m.reciprocal_rank == 0.0
        assert m.first_hit_rank is None

    def test_recall_at_3(self) -> None:
        q = GoldenQuery(id="q1", question="q", expected_files=["a.py"], expected_symbols=[])
        results = [
            _make_result("x.py", "x"),
            _make_result("y.py", "y"),
            _make_result("a.py", "a"),
        ]
        m = compute_query_metrics(q, results)
        assert m.recall_at_1 == 0.0
        assert m.recall_at_3 == 1.0
        assert m.first_hit_rank == 3
        assert abs(m.reciprocal_rank - 1 / 3) < 1e-6

    def test_empty_results(self) -> None:
        q = GoldenQuery(id="q1", question="q", expected_files=["a.py"], expected_symbols=[])
        m = compute_query_metrics(q, [])
        assert m.recall_at_1 == 0.0
        assert m.file_hit is False

    def test_no_expected_target_excluded_from_summary(self) -> None:
        scored = compute_query_metrics(
            GoldenQuery(id="q1", question="q1", expected_files=["a.py"]),
            [_make_result("a.py", "x")],
        )
        negative = compute_query_metrics(
            GoldenQuery(id="q2", question="negative", expected_files=[], expected_symbols=[]),
            [_make_result("b.py", "x")],
        )
        summary = compute_metrics([scored, negative])
        assert negative.has_expected_target is False
        assert summary.total == 2
        assert summary.recall_at_1 == 1.0

    def test_aggregate(self) -> None:
        queries_metrics = [
            compute_query_metrics(
                GoldenQuery(id="q1", question="q1", expected_files=["a.py"]),
                [_make_result("a.py", "x")],
            ),
            compute_query_metrics(
                GoldenQuery(id="q2", question="q2", expected_files=["a.py"]),
                [_make_result("b.py", "x")],
            ),
        ]
        summary = compute_metrics(queries_metrics)
        assert summary.total == 2
        assert summary.recall_at_1 == 0.5
        assert summary.mrr == 0.5

    def test_mean_reciprocal_rank(self) -> None:
        queries_metrics = [
            compute_query_metrics(
                GoldenQuery(id="q1", question="q", expected_files=["a.py"]),
                [_make_result("a.py", "x")],
            ),
            compute_query_metrics(
                GoldenQuery(id="q2", question="q", expected_files=["a.py"]),
                [],
            ),
        ]
        mrr = mean_reciprocal_rank(queries_metrics)
        assert abs(mrr - 0.5) < 1e-6


# ---------------------------------------------------------------------------
# eval_service.py
# ---------------------------------------------------------------------------


class TestEvalService:
    def test_query_mode_builds_matching_retriever(self, monkeypatch) -> None:
        from code_rag.services.eval_service import EvalService

        service = EvalService()
        dataset = GoldenDataset(
            name="t",
            queries=[
                GoldenQuery(
                    id="q1",
                    question="q",
                    expected_files=["a.py"],
                    mode="lexical",
                )
            ],
        )
        built_modes: list[str] = []
        fake_resolved = object()

        monkeypatch.setattr(
            "code_rag.services.eval_service.resolve_repo", lambda *_, **__: fake_resolved
        )

        def build_retriever(_resolved, mode: str):
            built_modes.append(mode)
            return {"mode": mode}

        def retrieve(retriever, _question, _resolved, _top_k, mode: str):
            assert retriever["mode"] == mode
            return [_make_result("a.py", "x")]

        monkeypatch.setattr(service, "_build_retriever", build_retriever)
        monkeypatch.setattr(service, "_retrieve", retrieve)

        summary = service.run(dataset, repo_path=".", mode="vector")
        assert built_modes == ["lexical"]
        assert summary.recall_at_1 == 1.0

    def test_compare_modes_runs_each_requested_mode(self, monkeypatch) -> None:
        from code_rag.services.eval_service import EvalService

        service = EvalService()
        dataset = GoldenDataset(
            name="t",
            queries=[
                GoldenQuery(id="q1", question="q", expected_files=["a.py"]),
            ],
        )
        called_modes: list[str] = []

        def fake_run(_dataset, *, repo_path, top_k, mode, ref=None):
            called_modes.append(mode)
            return compute_metrics(
                [
                    compute_query_metrics(
                        GoldenQuery(id=f"{mode}-q", question="q", expected_files=["a.py"]),
                        [_make_result("a.py", mode)],
                    )
                ]
            )

        monkeypatch.setattr(service, "run", fake_run)

        comparison = service.compare_modes(
            dataset,
            repo_path=".",
            modes=["vector", "lexical", "hybrid"],
            top_k=8,
        )

        assert called_modes == ["vector", "lexical", "hybrid"]
        assert list(comparison) == ["vector", "lexical", "hybrid"]
        assert comparison["hybrid"].recall_at_1 == 1.0


# ---------------------------------------------------------------------------
# report.py
# ---------------------------------------------------------------------------


class TestReports:
    def test_json_report(self, tmp_path: Path) -> None:
        q = GoldenQuery(id="q1", question="q1", expected_files=["a.py"], expected_symbols=[])
        queries_metrics = [
            compute_query_metrics(q, [_make_result("a.py", "x")]),
        ]
        summary = compute_metrics(queries_metrics)
        out = tmp_path / "report.json"
        path = write_json_report(
            summary, out, dataset_name="t", repo_path="/r", top_k=8, mode="vector"
        )
        assert path == out
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["dataset"] == "t"
        assert data["summary"]["recall_at_1"] == 1.0
        assert data["per_query"][0]["file_hit"] is True

    def test_markdown_report(self, tmp_path: Path) -> None:
        q = GoldenQuery(id="q1", question="q1", expected_files=["a.py"], expected_symbols=[])
        queries_metrics = [
            compute_query_metrics(q, [_make_result("a.py", "x")]),
        ]
        summary = compute_metrics(queries_metrics)
        out = tmp_path / "report.md"
        path = write_markdown_report(
            summary, out, dataset_name="t", repo_path="/r", top_k=8, mode="vector"
        )
        assert path == out
        text = out.read_text(encoding="utf-8")
        assert "# Retrieval Eval Report" in text
        assert "Recall@1" in text
        assert "100.00%" in text or "1.00" in text

    def test_render_markdown_in_memory(self) -> None:
        q = GoldenQuery(id="q1", question="q1", expected_files=["a.py"], expected_symbols=[])
        queries_metrics = [
            compute_query_metrics(q, [_make_result("a.py", "x")]),
        ]
        summary = compute_metrics(queries_metrics)
        text = render_markdown(summary, dataset_name="t", repo_path="/r", top_k=8, mode="vector")
        assert "## Summary" in text

    def test_comparison_json_report(self, tmp_path: Path) -> None:
        summary = compute_metrics(
            [
                compute_query_metrics(
                    GoldenQuery(id="q1", question="q", expected_files=["a.py"]),
                    [_make_result("a.py", "x")],
                )
            ]
        )
        out = tmp_path / "compare.json"
        path = write_comparison_json_report(
            {"vector": summary, "hybrid": summary},
            out,
            dataset_name="t",
            repo_path="/r",
            top_k=8,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["dataset"] == "t"
        assert list(data["modes"]) == ["vector", "hybrid"]
        assert data["modes"]["hybrid"]["summary"]["recall_at_8"] == 1.0

    def test_comparison_markdown_report(self, tmp_path: Path) -> None:
        summary = compute_metrics(
            [
                compute_query_metrics(
                    GoldenQuery(id="q1", question="q", expected_files=["a.py"]),
                    [_make_result("a.py", "x")],
                )
            ]
        )
        text = render_comparison_markdown(
            {"vector": summary, "hybrid": summary},
            dataset_name="t",
            repo_path="/r",
            top_k=8,
        )
        assert "# Retrieval Eval Comparison" in text
        assert "| vector |" in text
        assert "| hybrid |" in text

        out = tmp_path / "compare.md"
        path = write_comparison_markdown_report(
            {"vector": summary, "hybrid": summary},
            out,
            dataset_name="t",
            repo_path="/r",
            top_k=8,
        )
        assert "Retrieval Eval Comparison" in path.read_text(encoding="utf-8")
