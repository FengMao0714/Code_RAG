"""Retriever 模块测试。

覆盖：
- _extract_keywords: 从查询文本中提取标识符和关键词
- boost_by_metadata: 根据关键词对检索结果做元数据排名提升
- ContextBuilder.build_context: 上下文格式化（含 score 元数据）
- retrieve_with_context: 集成测试（index → retrieve → 验证召回质量）

不依赖真实 Embedding 模型或网络。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from code_rag.indexer.chunker import CodeChunk
from code_rag.retriever.retriever import ContextBuilder, Retriever
from code_rag.store.vector_store import ChromaStore, SearchResult

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_chunk(
    file_path: str = "app.py",
    name: str = "main",
    chunk_type: str = "function",
    source: str = "def main(): pass",
    start_line: int = 1,
    end_line: int = 1,
    parent: str | None = None,
    language: str = "python",
    file_hash: str = "abc123",
) -> CodeChunk:
    """快速构造 CodeChunk。"""
    return CodeChunk(
        file_path=file_path,
        language=language,
        chunk_type=chunk_type,
        name=name,
        start_line=start_line,
        end_line=end_line,
        parent=parent,
        file_hash=file_hash,
        source=source,
        token_count=10,
    )


def _make_result(
    chunk: CodeChunk,
    score: float = 0.5,
) -> SearchResult:
    """快速构造 SearchResult。"""
    return SearchResult(chunk=chunk, score=score)


# ===========================================================================
# _extract_keywords
# ===========================================================================


class TestExtractKeywords:
    """测试 _extract_keywords 关键词提取。"""

    def test_extracts_filename(self) -> None:
        """从查询中提取文件名。"""
        keywords = Retriever._extract_keywords("cli.py 在哪里")
        assert "cli.py" in keywords

    def test_extracts_function_name(self) -> None:
        """从查询中提取函数名。"""
        keywords = Retriever._extract_keywords("scanner 是怎么过滤文件的")
        assert "scanner" in keywords

    def test_extracts_class_name(self) -> None:
        """从查询中提取类名。"""
        keywords = Retriever._extract_keywords("CodeChunker 怎么切分")
        assert "codechunker" in keywords

    def test_extracts_camelcase(self) -> None:
        """提取 camelCase 标识符。"""
        keywords = Retriever._extract_keywords("getCollectionName 在哪里定义")
        assert "getcollectionname" in keywords

    def test_extracts_snake_case(self) -> None:
        """提取 snake_case 标识符。"""
        keywords = Retriever._extract_keywords("build_context 方法")
        assert "build_context" in keywords

    def test_extracts_chinese_keywords(self) -> None:
        """提取中文关键词。"""
        keywords = Retriever._extract_keywords("CLI 入口在哪里")
        assert "入口" in keywords

    def test_filters_stop_words(self) -> None:
        """过滤常见停用词。"""
        keywords = Retriever._extract_keywords("this is the function that does it")
        assert "this" not in keywords
        assert "the" not in keywords
        assert "that" not in keywords
        assert "does" not in keywords
        assert "function" in keywords

    def test_deduplicates(self) -> None:
        """去重。"""
        keywords = Retriever._extract_keywords("scanner scanner scanner")
        assert keywords.count("scanner") == 1

    def test_empty_query(self) -> None:
        """空查询返回空列表。"""
        assert Retriever._extract_keywords("") == []

    def test_extracts_toml_extension(self) -> None:
        """提取 .toml 扩展名。"""
        keywords = Retriever._extract_keywords("pyproject.toml 配置")
        assert "pyproject.toml" in keywords

    def test_extracts_multiple_keywords(self) -> None:
        """一次提取多个不同关键词。"""
        keywords = Retriever._extract_keywords("chunker 切分 function 和 class")
        kw_set = set(keywords)
        assert "chunker" in kw_set
        assert "function" in kw_set
        assert "class" in kw_set


# ===========================================================================
# boost_by_metadata
# ===========================================================================


class TestBoostByMetadata:
    """测试 boost_by_metadata 元数据排名提升。"""

    def test_boost_file_path_match(self) -> None:
        """file_path 匹配查询关键词的 chunk 被提升到前面。"""
        cli_chunk = _make_chunk(file_path="src/code_rag/cli.py", name="app")
        scanner_chunk = _make_chunk(file_path="src/code_rag/indexer/scanner.py", name="scan")
        readme_chunk = _make_chunk(file_path="README.md", name="README", language="doc")

        results = [
            _make_result(readme_chunk, 0.3),
            _make_result(scanner_chunk, 0.4),
            _make_result(cli_chunk, 0.5),
        ]

        boosted = Retriever.boost_by_metadata(results, "这个项目的 CLI 入口在哪里？")

        assert len(boosted) == 3
        assert boosted[0].chunk.file_path == "src/code_rag/cli.py"

    def test_boost_name_match(self) -> None:
        """name 匹配查询关键词的 chunk 被提升。"""
        login_chunk = _make_chunk(name="login", source="def login(): pass")
        other_chunk = _make_chunk(name="process", source="def process(): pass")

        results = [
            _make_result(other_chunk, 0.2),
            _make_result(login_chunk, 0.5),
        ]

        boosted = Retriever.boost_by_metadata(results, "login 函数在哪里")

        assert boosted[0].chunk.name == "login"

    def test_boost_pyproject_match(self) -> None:
        """cli.py chunk 被 CLI 入口问题 boost 到前面。"""
        pyproject_chunk = _make_chunk(
            file_path="pyproject.toml",
            name="pyproject.toml",
            chunk_type="doc",
            language="doc",
        )
        other_chunk = _make_chunk(file_path="src/config.py", name="Settings")
        cli_chunk = _make_chunk(file_path="src/code_rag/cli.py", name="app")

        results = [
            _make_result(pyproject_chunk, 0.3),
            _make_result(other_chunk, 0.4),
            _make_result(cli_chunk, 0.5),
        ]
        boosted = Retriever.boost_by_metadata(results, "CLI 入口在哪里")
        assert boosted[0].chunk.file_path == "src/code_rag/cli.py"

    def test_no_boost_without_match(self) -> None:
        """没有匹配时保持原始顺序。"""
        chunk_a = _make_chunk(file_path="a.py", name="foo")
        chunk_b = _make_chunk(file_path="b.py", name="bar")

        results = [
            _make_result(chunk_a, 0.3),
            _make_result(chunk_b, 0.4),
        ]

        boosted = Retriever.boost_by_metadata(results, "某个无关问题")

        # "某个" "无关" "问题" 都是中文关键词但不在 stop words 中
        # 它们不会匹配 a.py/b.py/foo/bar，所以顺序不变
        assert boosted[0].chunk.file_path == "a.py"
        assert boosted[1].chunk.file_path == "b.py"

    def test_no_boost_empty_results(self) -> None:
        """空结果列表不崩溃。"""
        assert Retriever.boost_by_metadata([], "query") == []

    def test_no_boost_empty_query(self) -> None:
        """空查询不改变顺序。"""
        chunk_a = _make_chunk(file_path="a.py", name="foo")
        results = [_make_result(chunk_a)]
        boosted = Retriever.boost_by_metadata(results, "")
        assert boosted == results

    def test_preserves_all_results(self) -> None:
        """boost 只重排，不删除任何结果。"""
        chunks = [_make_chunk(file_path=f"f{i}.py", name=f"func{i}") for i in range(5)]
        results = [_make_result(c, score=0.1 * i) for c, i in zip(chunks, range(5))]

        boosted = Retriever.boost_by_metadata(results, "func0 函数")

        assert len(boosted) == 5
        boosted_files = {r.chunk.file_path for r in boosted}
        assert boosted_files == {f"f{i}.py" for i in range(5)}

    def test_boost_parent_match(self) -> None:
        """parent 字段匹配时也被 boost。"""
        method_chunk = _make_chunk(
            name="parse_file",
            parent="CodeParser",
            file_path="parser.py",
        )
        other_chunk = _make_chunk(name="helper", file_path="utils.py")

        results = [
            _make_result(other_chunk, 0.3),
            _make_result(method_chunk, 0.5),
        ]

        boosted = Retriever.boost_by_metadata(results, "CodeParser 类")

        assert boosted[0].chunk.parent == "CodeParser"


# ===========================================================================
# ContextBuilder.build_context
# ===========================================================================


class TestContextBuilderScore:
    """测试 build_context 中 score 元数据的输出。"""

    def test_context_includes_score(self) -> None:
        """上下文包含检索距离分数。"""
        chunk = _make_chunk(
            file_path="src/cli.py",
            name="app",
            chunk_type="function",
            source="def app(): pass",
        )
        context = ContextBuilder.build_context([chunk], scores=[0.3210])

        assert "0.3210" in context
        assert "src/cli.py" in context
        assert "function" in context

    def test_context_shows_na_when_no_scores(self) -> None:
        """未传入 scores 时显示 N/A。"""
        chunk = _make_chunk()
        context = ContextBuilder.build_context([chunk])

        assert "N/A" in context

    def test_context_preserves_all_metadata(self) -> None:
        """上下文包含所有元数据字段。"""
        chunk = _make_chunk(
            file_path="src/indexer/scanner.py",
            name="scan_files",
            chunk_type="function",
            start_line=10,
            end_line=25,
            language="python",
            source="def scan_files(): pass",
        )
        context = ContextBuilder.build_context([chunk], scores=[0.42])

        assert "scanner.py" in context
        assert "scan_files" in context
        assert "function" in context
        assert "10" in context
        assert "25" in context
        assert "python" in context
        assert "0.42" in context

    def test_context_multiple_chunks_with_scores(self) -> None:
        """多个 chunk 的上下文各自包含对应分数。"""
        chunks = [
            _make_chunk(file_path="a.py", name="foo", source="def foo(): pass"),
            _make_chunk(file_path="b.py", name="bar", source="def bar(): pass"),
        ]
        context = ContextBuilder.build_context(chunks, scores=[0.1, 0.9])

        assert "0.1" in context
        assert "0.9" in context
        # 分隔符
        assert "---" in context


# ===========================================================================
# retrieve_with_context 集成测试（使用 FakeEmbedder + 真实 ChromaDB）
# ===========================================================================


class TestRetrieveIntegration:
    """测试 Retriever 检索 + ContextBuilder 组装的集成流程。"""

    @pytest.fixture()
    def settings(self, tmp_path: Path):
        """临时 Settings。"""
        return SimpleNamespace(
            chroma_persist_path=tmp_path / "chroma",
            retrieval_top_k=8,
            retrieval_score_threshold=1.0,  # 不过滤
        )

    @pytest.fixture()
    def indexed_repo(self, tmp_path: Path, settings):
        """索引一组测试文件。"""
        from tests.conftest import FakeEmbedder

        files = {
            "src/code_rag/cli.py": (
                "import typer\n\napp = typer.Typer()\n\n"
                "@app.command()\ndef ask(repo_path, question):\n    pass\n"
            ),
            "src/code_rag/indexer/scanner.py": (
                "class RepoScanner:\n    IGNORE_DIRS = {'.git', 'node_modules'}\n\n"
                "    def scan(self):\n        pass\n"
            ),
            "src/code_rag/indexer/chunker.py": (
                "class CodeChunker:\n    def chunk(self, symbols):\n        pass\n\n"
                "    def _split_oversized_function(self, sym):\n        pass\n"
            ),
            "pyproject.toml": (
                "[project]\nname = 'code-rag'\n\n[project.scripts]\ncode-rag = 'code_rag.cli:app'\n"
            ),
        }

        # 写文件
        for rel_path, content in files.items():
            file_path = tmp_path / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        # 切片并入库
        embedder = FakeEmbedder()
        store = ChromaStore(settings)
        coll_name = ChromaStore.get_collection_name(tmp_path)

        from code_rag.indexer.chunker import CodeChunker

        chunker = CodeChunker(max_chunk_tokens=512)
        all_chunks: list[CodeChunk] = []

        for rel_path, content in files.items():
            lang = "python" if rel_path.endswith(".py") else "doc"
            file_hash = f"hash_{rel_path}"
            file_chunks = chunker.chunk(
                [],  # 空 symbols，只靠 full_source 生成 module_summary/doc
                file_hash=file_hash,
                full_source=content,
            )
            # 修正 file_path（chunker 从 symbols 获取，这里手动修正）
            for c in file_chunks:
                c.file_path = rel_path
                c.language = lang
            all_chunks.extend(file_chunks)

        embeddings = embedder.embed_texts([c.source for c in all_chunks])
        store.upsert_chunks(coll_name, all_chunks, embeddings)

        return tmp_path

    def test_retrieve_cli_entry_point(self, tmp_path: Path, settings, indexed_repo) -> None:
        """检索 'CLI 入口在哪里' 应该能召回到 cli.py。"""
        from tests.conftest import FakeEmbedder

        # 需要 monkeypatch Embedder
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "code_rag.indexer.embedder.Embedder.get_instance",
                lambda s: FakeEmbedder(),
            )
            retriever = Retriever(settings)
            results = retriever.retrieve(
                "这个项目的 CLI 入口在哪里？",
                indexed_repo,
            )

        assert len(results) > 0
        # boost 应该把 cli.py 排到前面
        assert results[0].chunk.file_path == "src/code_rag/cli.py"

    def test_retrieve_scanner_filter(self, tmp_path: Path, settings, indexed_repo) -> None:
        """检索 'scanner 过滤文件' 应该能召回到 scanner.py。"""
        from tests.conftest import FakeEmbedder

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "code_rag.indexer.embedder.Embedder.get_instance",
                lambda s: FakeEmbedder(),
            )
            retriever = Retriever(settings)
            results = retriever.retrieve(
                "scanner 是怎么过滤文件的？",
                indexed_repo,
            )

        assert len(results) > 0
        assert results[0].chunk.file_path == "src/code_rag/indexer/scanner.py"

    def test_retrieve_chunker_split(self, tmp_path: Path, settings, indexed_repo) -> None:
        """检索 'chunker 切分函数' 应该能召回到 chunker.py。"""
        from tests.conftest import FakeEmbedder

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "code_rag.indexer.embedder.Embedder.get_instance",
                lambda s: FakeEmbedder(),
            )
            retriever = Retriever(settings)
            results = retriever.retrieve(
                "chunker 如何切分函数？",
                indexed_repo,
            )

        assert len(results) > 0
        assert results[0].chunk.file_path == "src/code_rag/indexer/chunker.py"

    def test_retrieve_pyproject(self, tmp_path: Path, settings, indexed_repo) -> None:
        """检索 'pyproject.toml 配置' 应该能召回到 pyproject.toml。"""
        from tests.conftest import FakeEmbedder

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "code_rag.indexer.embedder.Embedder.get_instance",
                lambda s: FakeEmbedder(),
            )
            retriever = Retriever(settings)
            results = retriever.retrieve(
                "pyproject.toml 中的 entry point 配置",
                indexed_repo,
            )

        assert len(results) > 0
        assert results[0].chunk.file_path == "pyproject.toml"

    def test_retrieve_with_context_has_scores(self, tmp_path: Path, settings, indexed_repo) -> None:
        """retrieve_with_context 返回的上下文包含 score 信息。"""
        from tests.conftest import FakeEmbedder

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "code_rag.indexer.embedder.Embedder.get_instance",
                lambda s: FakeEmbedder(),
            )
            retriever = Retriever(settings)
            result = retriever.retrieve_with_context(
                "CLI 入口",
                indexed_repo,
            )

        assert result.context
        # 上下文中应包含数字分数（不是 N/A）
        import re as re_mod

        assert re_mod.search(r"score: \d+\.\d+", result.context)
