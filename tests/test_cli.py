"""CLI smoke 测试。

覆盖：
- `code-rag --help` 能正常输出
- `code-rag status` 对未索引仓库不崩溃
- `code-rag index` 最小闭环（monkeypatch fake embedder）
- `code-rag ask` 最小闭环（monkeypatch fake embedder + fake LLM）
- `code-rag list` 空状态不崩溃

使用 Typer CliRunner，所有 fake 组件通过 conftest 中的 fixtures 提供。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from code_rag.cli import app
from code_rag.store.vector_store import ChromaStore
from tests.conftest import FakeEmbedder, FakeLLMClient

runner = CliRunner()


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestHelp:
    """测试 CLI 帮助信息。"""

    def test_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "code-rag" in result.output.lower() or "代码" in result.output


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestStatusCommand:
    """测试 status 命令。"""

    def test_unindexed_repo_no_crash(self, tmp_path: Path, tmp_settings) -> None:
        """对未索引仓库执行 status 不崩溃，显示尚未索引提示。"""
        with patch("code_rag.cli.get_settings", return_value=tmp_settings):
            result = runner.invoke(app, ["status", str(tmp_path)])
        assert result.exit_code == 0
        assert "尚未索引" in result.output

    def test_indexed_repo_shows_stats(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        """已索引仓库显示统计信息。"""
        # 先写入数据
        store = ChromaStore(tmp_settings)
        coll_name = ChromaStore.get_collection_name(tmp_path)

        chunks = [
            _make_cli_chunk("app.py", "main", "function", "def main(): pass", 1, 2),
        ]
        embeddings = [FakeEmbedder._hash_embed("def main(): pass")]
        store.upsert_chunks(coll_name, chunks, embeddings)

        with patch("code_rag.cli.get_settings", return_value=tmp_settings):
            result = runner.invoke(app, ["status", str(tmp_path)])

        assert result.exit_code == 0
        assert "总切片数" in result.output
        assert "1" in result.output


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


class TestIndexCommand:
    """测试 index 命令。"""

    def test_index_creates_chunks(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        """index 命令能扫描文件、生成切片并写入 ChromaDB。"""
        # 准备测试仓库
        (tmp_path / "hello.py").write_text(
            "def greet(name: str) -> str:\n    return f'hi {name}'\n",
            encoding="utf-8",
        )

        with (
            patch("code_rag.cli.get_settings", return_value=tmp_settings),
            patch("code_rag.indexer.embedder.Embedder.get_instance", return_value=patch_embedder),
        ):
            result = runner.invoke(app, ["index", str(tmp_path)])

        assert result.exit_code == 0
        assert "索引完成" in result.output

        # 验证 ChromaDB 中有数据
        store = ChromaStore(tmp_settings)
        coll_name = ChromaStore.get_collection_name(tmp_path)
        stats = store.get_stats(coll_name)
        assert stats["exists"] is True
        assert stats["total_chunks"] >= 1

    def test_index_empty_dir_no_crash(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        """对空目录执行 index 不崩溃。"""
        with (
            patch("code_rag.cli.get_settings", return_value=tmp_settings),
            patch("code_rag.indexer.embedder.Embedder.get_instance", return_value=patch_embedder),
        ):
            result = runner.invoke(app, ["index", str(tmp_path)])

        # 空目录没有可索引文件，可能输出"未生成任何代码切片"或直接完成
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------


class TestAskCommand:
    """测试 ask 命令。"""

    def test_ask_returns_answer(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        """ask 命令能检索上下文并调用 LLM 生成回答。"""
        # 1. 先索引
        (tmp_path / "app.py").write_text(
            "def hello() -> str:\n    return 'world'\n",
            encoding="utf-8",
        )

        with (
            patch("code_rag.cli.get_settings", return_value=tmp_settings),
            patch("code_rag.indexer.embedder.Embedder.get_instance", return_value=patch_embedder),
        ):
            runner.invoke(app, ["index", str(tmp_path)])

        # 2. 提问
        with (
            patch("code_rag.cli.get_settings", return_value=tmp_settings),
            patch("code_rag.indexer.embedder.Embedder.get_instance", return_value=patch_embedder),
            patch("code_rag.cli.LLMClient", FakeLLMClient),
        ):
            result = runner.invoke(app, ["ask", str(tmp_path), "what does hello do?"])

        assert result.exit_code == 0
        # FakeLLM 输出包含 context_length= 和 question=（fake answer 前缀可能被编码过滤）
        assert "context_length=" in result.output
        assert "what does hello do?" in result.output

    def test_ask_unindexed_repo(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        """对未索引仓库提问，应提示未找到相关代码。"""
        with (
            patch("code_rag.cli.get_settings", return_value=tmp_settings),
            patch("code_rag.indexer.embedder.Embedder.get_instance", return_value=patch_embedder),
            patch("code_rag.cli.LLMClient", FakeLLMClient),
        ):
            result = runner.invoke(app, ["ask", str(tmp_path), "hello?"])

        assert result.exit_code == 0
        assert "未找到相关代码" in result.output

    def test_ask_unindexed_no_coll_created(
        self, tmp_path: Path, tmp_settings, patch_embedder
    ) -> None:
        """对未索引仓库提问后不会创建空 collection。"""
        coll_name = ChromaStore.get_collection_name(tmp_path)

        with (
            patch("code_rag.cli.get_settings", return_value=tmp_settings),
            patch("code_rag.indexer.embedder.Embedder.get_instance", return_value=patch_embedder),
            patch("code_rag.cli.LLMClient", FakeLLMClient),
        ):
            runner.invoke(app, ["ask", str(tmp_path), "hello?"])

        store = ChromaStore(tmp_settings)
        stats = store.get_stats(coll_name)
        assert stats["exists"] is False


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearchCommand:
    """测试 search 命令。"""

    def test_search_default_mode_is_hybrid(self, tmp_path: Path, tmp_settings) -> None:
        """search 未显式传 --mode 时应使用 hybrid，便于展示默认最佳路径。"""
        store = ChromaStore(tmp_settings)
        coll_name = ChromaStore.get_collection_name(tmp_path)
        chunk = _make_cli_chunk("app.py", "hello", "function", "def hello(): pass", 1, 1)
        store.upsert_chunks(coll_name, [chunk], [FakeEmbedder._hash_embed(chunk.source)])

        called_with: dict[str, str] = {}

        def _fake_build(mode: str, _settings, _resolved):
            called_with["mode"] = mode

            def _search(_query, _top_k, _score_threshold):
                from code_rag.store.vector_store import SearchResult

                return [SearchResult(chunk=chunk, score=0.01)]

            return _search

        with (
            patch("code_rag.cli.get_settings", return_value=tmp_settings),
            patch("code_rag.services.query_service.build_retriever", side_effect=_fake_build),
        ):
            result = runner.invoke(app, ["search", str(tmp_path), "hello"])

        assert result.exit_code == 0, result.output
        assert called_with["mode"] == "hybrid"
        assert "mode=hybrid" in result.output

    def test_search_rejects_invalid_mode(self, tmp_path: Path, tmp_settings) -> None:
        """非法 mode 应产生清晰错误。"""
        store = ChromaStore(tmp_settings)
        coll_name = ChromaStore.get_collection_name(tmp_path)
        chunk = _make_cli_chunk("app.py", "hello", "function", "def hello(): pass", 1, 1)
        store.upsert_chunks(coll_name, [chunk], [FakeEmbedder._hash_embed(chunk.source)])

        with patch("code_rag.cli.get_settings", return_value=tmp_settings):
            result = runner.invoke(app, ["search", str(tmp_path), "hello", "--mode", "bad"])

        assert result.exit_code == 1
        assert "不支持的检索模式" in result.output


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------


class TestEvalCommand:
    """测试 eval 命令。"""

    def test_eval_compare_modes_renders_table(self, tmp_path: Path, tmp_settings) -> None:
        """eval --compare-modes 应输出多模式对比表并写报告。"""
        from code_rag.evaluation.dataset import GoldenQuery
        from code_rag.evaluation.metrics import compute_metrics, compute_query_metrics

        class _Dataset:
            name = "demo"
            queries = [GoldenQuery(id="q1", question="q", expected_files=["a.py"])]

        class _FakeEvalService:
            def __init__(self, _settings) -> None:
                self.summary = compute_metrics(
                    [
                        compute_query_metrics(
                            GoldenQuery(id="q1", question="q", expected_files=["a.py"]),
                            [_make_cli_result("a.py", "x")],
                        )
                    ]
                )

            def load(self, _dataset):
                return _Dataset()

            def compare_modes(self, _dataset, *, repo_path, modes, top_k, ref=None):
                return {mode: self.summary for mode in modes}

            def write_comparison_reports(
                self,
                comparison,
                *,
                dataset_name,
                repo_path,
                top_k,
                output_json=None,
                output_markdown=None,
            ):
                from code_rag.evaluation.report import ReportPaths

                if output_json:
                    Path(output_json).write_text("{}", encoding="utf-8")
                if output_markdown:
                    Path(output_markdown).write_text("# report\n", encoding="utf-8")
                return ReportPaths(
                    json_path=Path(output_json) if output_json else None,
                    markdown_path=Path(output_markdown) if output_markdown else None,
                )

        out_json = tmp_path / "compare.json"
        out_md = tmp_path / "compare.md"
        with (
            patch("code_rag.cli.get_settings", return_value=tmp_settings),
            patch("code_rag.services.EvalService", _FakeEvalService),
        ):
            result = runner.invoke(
                app,
                [
                    "eval",
                    str(tmp_path),
                    "--compare-modes",
                    "vector,hybrid",
                    "--output",
                    str(out_json),
                    "--markdown",
                    str(out_md),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "模式对比" in result.output
        assert "vector" in result.output
        assert "hybrid" in result.output
        assert out_json.exists()
        assert out_md.exists()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestListCommand:
    """测试 list 命令。"""

    def test_list_empty(self, tmp_path: Path, tmp_settings) -> None:
        """无索引时 list 不崩溃。"""
        with patch("code_rag.cli.get_settings", return_value=tmp_settings):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert "暂无" in result.output


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_cli_chunk(
    file_path: str,
    name: str,
    chunk_type: str,
    source: str,
    start_line: int,
    end_line: int,
) -> object:
    """构造 CodeChunk 用于预填充 ChromaDB。"""
    from code_rag.indexer.chunker import CodeChunk

    return CodeChunk(
        file_path=file_path,
        language="python",
        chunk_type=chunk_type,
        name=name,
        start_line=start_line,
        end_line=end_line,
        parent=None,
        file_hash="testhash",
        source=source,
        token_count=10,
    )


def _make_cli_result(file_path: str, name: str):
    from code_rag.store.vector_store import SearchResult

    return SearchResult(
        chunk=_make_cli_chunk(file_path, name, "function", "x", 1, 1),
        score=0.1,
    )
