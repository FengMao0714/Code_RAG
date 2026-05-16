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
