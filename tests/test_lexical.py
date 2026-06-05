"""词法检索器 + RRF 重排序 + Hybrid 检索器测试。

不依赖真实 ChromaDB 远程或 Embedding 模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_rag.indexer.chunker import CodeChunk
from code_rag.retriever.hybrid import HybridRetriever
from code_rag.retriever.lexical import LexicalRetriever
from code_rag.retriever.rerank import RRFReranker
from code_rag.store.vector_store import SearchResult

# ---------------------------------------------------------------------------
# 辅助：mock collection
# ---------------------------------------------------------------------------


@dataclass
class _MockMeta:
    file_path: str
    name: str
    chunk_type: str
    start_line: int
    end_line: int
    language: str = "python"
    parent: str = ""
    file_hash: str = "h"


class _MockCollection:
    """模拟 ChromaDB collection：仅提供 LexicalRetriever 所需的接口。"""

    def __init__(self, chunks: list[tuple[str, dict, str]]) -> None:
        # list of (id, metadata, document)
        self._chunks = chunks

    def count(self) -> int:
        return len(self._chunks)

    def get(self, *, limit: int, include: list[str]) -> dict[str, Any]:
        ids = [c[0] for c in self._chunks[:limit]]
        metas = [c[1] for c in self._chunks[:limit]]
        docs = [c[2] for c in self._chunks[:limit]]
        return {"ids": ids, "metadatas": metas, "documents": docs}


class _MockStore:
    """模拟 ChromaStore，仅暴露 LexicalRetriever 需要的 ``_client``。"""

    def __init__(self, chunks: list[tuple[str, dict, str]]) -> None:
        self._client = _MockClient(chunks)


class _MockClient:
    def __init__(self, chunks: list[tuple[str, dict, str]]) -> None:
        self._chunks = chunks

    def get_collection(self, *, name: str) -> _MockCollection:
        return _MockCollection(self._chunks)


def _chunks_for_lexical() -> list[tuple[str, dict, str]]:
    return [
        (
            "id1",
            _make_meta("src/code_rag/cli.py", "app", "module_summary"),
            "import typer\napp = typer.Typer()\n",
        ),
        (
            "id2",
            _make_meta("src/code_rag/cli.py", "app", "function"),
            "def setup_logging(verbose: bool = False) -> None:\n    pass\n",
        ),
        (
            "id3",
            _make_meta("src/code_rag/retriever/retriever.py", "Retriever", "class"),
            "class Retriever:\n    def retrieve(self, query, repo_path):\n        pass\n",
        ),
        (
            "id4",
            _make_meta("README.md", "README.md", "doc"),
            "# Code_RAG\n代码知识库 RAG 问答助手\n",
        ),
    ]


def _make_meta(file_path: str, name: str, chunk_type: str) -> dict:
    return {
        "file_path": file_path,
        "name": name,
        "chunk_type": chunk_type,
        "start_line": 1,
        "end_line": 10,
        "language": "python" if chunk_type != "doc" else "doc",
        "parent": "",
        "file_hash": "h",
    }


# ---------------------------------------------------------------------------
# LexicalRetriever
# ---------------------------------------------------------------------------


class TestLexicalRetriever:
    def test_keyword_extraction(self) -> None:
        kws = LexicalRetriever._extract_keywords("CLI 入口在哪里")
        assert "cli" in kws
        assert "入口" in kws

    def test_filename_match_higher_score(self, tmp_path: Path) -> None:
        store = _MockStore(_chunks_for_lexical())
        repo = tmp_path
        lex = LexicalRetriever(store, repo)
        results = lex.search("cli.py", top_k=4)
        # cli.py 出现在 file_path 中，至少应该排在前面
        assert any("cli.py" in r.chunk.file_path for r in results)
        assert len(results) >= 1

    def test_no_keyword_returns_empty(self, tmp_path: Path) -> None:
        store = _MockStore(_chunks_for_lexical())
        lex = LexicalRetriever(store, tmp_path)
        # 停用词 only
        results = lex.search("the and", top_k=4)
        assert results == []

    def test_chinese_keyword(self, tmp_path: Path) -> None:
        # 在 chunk 源码中加入中文以验证中文关键词提取与匹配
        chunks = _chunks_for_lexical() + [
            (
                "id5",
                _make_meta("src/code_rag/cli.py", "入口函数", "function"),
                "def main_entry():\n    # CLI 入口在这里\n    return None\n",
            ),
        ]
        store = _MockStore(chunks)
        lex = LexicalRetriever(store, tmp_path)
        results = lex.search("入口", top_k=4)
        assert len(results) >= 1
        # 命中的 chunk 源码应包含 "入口"
        assert any("入口" in r.chunk.source for r in results)


# ---------------------------------------------------------------------------
# RRFReranker
# ---------------------------------------------------------------------------


def _make_search_result(file_path: str, score: float) -> SearchResult:
    chunk = CodeChunk(
        file_path=file_path,
        language="python",
        chunk_type="function",
        name="x",
        start_line=1,
        end_line=1,
        parent=None,
        file_hash="h",
        source="x",
        token_count=0,
    )
    return SearchResult(chunk=chunk, score=score)


class TestRRFReranker:
    def test_basic_fusion(self) -> None:
        reranker = RRFReranker()
        v = [_make_search_result("a.py", 0.1), _make_search_result("b.py", 0.2)]
        lex = [_make_search_result("a.py", 10.0), _make_search_result("c.py", 8.0)]
        merged = reranker.rerank({"vector": v, "lexical": lex}, top_k=3)
        # a.py 在两通道都出现，应该排第一
        assert merged[0].chunk.file_path == "a.py"
        # stage 标签存在
        assert hasattr(merged[0], "stage")

    def test_top_k_truncate(self) -> None:
        reranker = RRFReranker()
        v = [_make_search_result(f"f{i}.py", 0.1 * i) for i in range(5)]
        merged = reranker.rerank({"vector": v}, top_k=2)
        assert len(merged) == 2

    def test_weights(self) -> None:
        from code_rag.retriever.rerank import RRFConfig

        reranker = RRFReranker(RRFConfig(weights={"vector": 2.0, "lexical": 0.0}))
        v = [_make_search_result("a.py", 0.1)]
        lex = [_make_search_result("a.py", 5.0), _make_search_result("b.py", 5.0)]
        merged = reranker.rerank({"vector": v, "lexical": lex}, top_k=3)
        # lexical 权重为 0，仅 vector 贡献分数
        assert merged[0].chunk.file_path == "a.py"


# ---------------------------------------------------------------------------
# HybridRetriever
# ---------------------------------------------------------------------------


class _StubVector:
    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results
        self.calls = 0

    def retrieve(self, query, repo_path, *, top_k=None, score_threshold=None):
        self.calls += 1
        return self._results


class _StubLexical:
    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results

    def search(self, query, *, top_k=8):
        return self._results


class TestHybridRetriever:
    def test_fuses_both_channels(self, tmp_path: Path) -> None:
        v = [_make_search_result("a.py", 0.1), _make_search_result("b.py", 0.2)]
        lex = [_make_search_result("c.py", 1.0)]
        hybrid = HybridRetriever(
            vector_retriever=_StubVector(v),  # type: ignore[arg-type]
            lexical_retriever=_StubLexical(lex),  # type: ignore[arg-type]
        )
        results = hybrid.search("q", tmp_path, top_k=5)
        assert len(results) == 3
        files = [r.chunk.file_path for r in results]
        assert "a.py" in files and "b.py" in files and "c.py" in files

    def test_empty_inputs(self, tmp_path: Path) -> None:
        hybrid = HybridRetriever(
            vector_retriever=_StubVector([]),  # type: ignore[arg-type]
            lexical_retriever=_StubLexical([]),  # type: ignore[arg-type]
        )
        results = hybrid.search("q", tmp_path, top_k=5)
        assert results == []
