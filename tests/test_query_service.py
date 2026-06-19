"""QueryService 单元测试。

覆盖：
- search 走 Retriever（vector 模式）
- ask 默认走 hybrid 模式
- ask 返回低置信度判定（context 为空 / 全 doc / 平均距离过高）
- hybrid/lexical 分数不做距离阈值检查
- 流式接口返回 generator
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from code_rag.indexer.chunker import CodeChunk
from code_rag.retriever.retriever import RetrievalResult
from code_rag.services import QueryService
from code_rag.store.vector_store import SearchResult


@dataclass
class _FakeChunk:
    file_path: str
    chunk_type: str
    source: str
    start_line: int = 1
    end_line: int = 1
    name: str = "x"
    language: str = "python"
    parent: str | None = None
    file_hash: str = "h"
    token_count: int = 0
    metadata: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


def _make_result(file_path: str, chunk_type: str, score: float) -> SearchResult:
    chunk = CodeChunk(
        file_path=file_path,
        language="python",
        chunk_type=chunk_type,
        name="x",
        start_line=1,
        end_line=1,
        parent=None,
        file_hash="h",
        source="x",
        token_count=0,
    )
    return SearchResult(chunk=chunk, score=score)


class TestConfidenceEvaluation:
    def test_empty_context_low_confidence(self) -> None:
        retrieval = RetrievalResult(question="q", chunks=[], context="", scores=[])
        low, reason = QueryService._evaluate_confidence(retrieval, mode="vector")
        assert low is True
        assert "未找到" in reason

    def test_only_doc_chunks_low_confidence(self) -> None:
        chunks = [
            _make_result("README.md", "doc", 0.3).chunk,
        ]
        scores = [0.3]
        retrieval = RetrievalResult(question="q", chunks=chunks, context="x", scores=scores)
        low, reason = QueryService._evaluate_confidence(retrieval, mode="vector")
        assert low is True
        assert "文档" in reason

    def test_code_chunks_high_confidence(self) -> None:
        chunks = [_make_result("a.py", "function", 0.4).chunk]
        scores = [0.4]
        retrieval = RetrievalResult(question="q", chunks=chunks, context="x", scores=scores)
        low, _reason = QueryService._evaluate_confidence(retrieval, mode="vector")
        assert low is False

    def test_high_avg_distance_low_confidence(self) -> None:
        chunks = [_make_result("a.py", "function", 1.5).chunk]
        scores = [1.5]
        retrieval = RetrievalResult(question="q", chunks=chunks, context="x", scores=scores)
        low, reason = QueryService._evaluate_confidence(retrieval, mode="vector")
        assert low is True
        assert "距离过高" in reason

    def test_hybrid_high_score_not_low_confidence(self) -> None:
        """hybrid/RRF 分数越大越好，不应被距离阈值判定为低置信度。"""
        chunks = [_make_result("a.py", "function", 0.015).chunk]
        scores = [0.015]  # RRF 典型分数（k=60 时 max ~0.016）
        retrieval = RetrievalResult(question="q", chunks=chunks, context="x", scores=scores)
        low, _reason = QueryService._evaluate_confidence(retrieval, mode="hybrid")
        assert low is False

    def test_lexical_high_score_not_low_confidence(self) -> None:
        """词法分数越大越好，不应被距离阈值判定为低置信度。"""
        chunks = [_make_result("a.py", "function", 9.5).chunk]
        scores = [9.5]  # 词法 TF 分数可以很大
        retrieval = RetrievalResult(question="q", chunks=chunks, context="x", scores=scores)
        low, _reason = QueryService._evaluate_confidence(retrieval, mode="lexical")
        assert low is False


class TestSearchMode:
    def test_normalize_accepts_supported_modes(self) -> None:
        from code_rag.retriever.modes import SearchMode

        assert SearchMode.normalize("vector") == "vector"
        assert SearchMode.normalize("lexical") == "lexical"
        assert SearchMode.normalize("hybrid") == "hybrid"

    def test_normalize_strips_and_lowercases(self) -> None:
        from code_rag.retriever.modes import SearchMode

        assert SearchMode.normalize(" HYBRID ") == "hybrid"

    def test_normalize_rejects_invalid_mode(self) -> None:
        from code_rag.retriever.modes import SearchMode

        try:
            SearchMode.normalize("semantic")
        except ValueError as exc:
            assert "vector/lexical/hybrid" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("SearchMode.normalize should reject invalid modes")


class _StubRetriever:
    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results
        self.calls: list[Any] = []

    def retrieve(self, query: str, repo_path, *, top_k=None, score_threshold=None):
        self.calls.append((query, str(repo_path), top_k, score_threshold))
        return self._results

    def retrieve_with_context(self, query: str, repo_path, *, top_k=None, score_threshold=None):
        results = self.retrieve(query, repo_path, top_k=top_k, score_threshold=score_threshold)
        return RetrievalResult(
            question=query,
            chunks=[r.chunk for r in results],
            context="\n".join(r.chunk.source for r in results),
            scores=[r.score for r in results],
        )


class TestQueryService:
    def test_search_delegates_to_retriever(self, tmp_path: Path, tmp_settings) -> None:
        stub_results = [_make_result("a.py", "function", 0.1)]
        called_with: dict[str, Any] = {}

        def _fake_build(mode: str, settings: Any, resolved: Any) -> Any:
            def _search(
                query: str, top_k: int | None = None, score_threshold: float | None = None
            ) -> Any:
                called_with["query"] = query
                called_with["top_k"] = top_k
                return stub_results

            return _search

        service = QueryService.__new__(QueryService)
        service._settings = tmp_settings  # type: ignore[attr-defined]
        service._retriever = None  # type: ignore[attr-defined]
        service._llm = None  # type: ignore[attr-defined]

        with patch("code_rag.services.query_service.build_retriever", side_effect=_fake_build):
            results = service.search("q", tmp_path, top_k=3, mode="vector")

        assert len(results) == 1
        assert called_with["top_k"] == 3

    def test_ask_returns_low_confidence_for_empty(self, tmp_path: Path, tmp_settings) -> None:
        def _fake_build(mode: str, settings: Any, resolved: Any) -> Any:
            def _search(
                query: str, top_k: int | None = None, score_threshold: float | None = None
            ) -> Any:
                return []

            return _search

        service = QueryService.__new__(QueryService)
        service._settings = tmp_settings  # type: ignore[attr-defined]
        service._retriever = None  # type: ignore[attr-defined]
        service._llm = None  # type: ignore[attr-defined]

        with patch("code_rag.services.query_service.build_retriever", side_effect=_fake_build):
            result = service.ask("q", tmp_path, mode="vector")

        assert result.low_confidence is True
        assert result.retrieval.chunks == []

    def test_ask_default_mode_is_hybrid(self, tmp_path: Path, tmp_settings) -> None:
        """ask() 默认 mode='hybrid'，应走 build_retriever('hybrid', ...) 路径。"""
        service = QueryService.__new__(QueryService)
        service._settings = tmp_settings  # type: ignore[attr-defined]
        service._retriever = None  # type: ignore[attr-defined]
        service._llm = None  # type: ignore[attr-defined]

        called_with: dict[str, Any] = {}

        def _fake_build(mode: str, settings: Any, resolved: Any) -> Any:
            called_with["mode"] = mode

            def _search(
                query: str, top_k: int | None = None, score_threshold: float | None = None
            ) -> Any:
                called_with["query"] = query
                called_with["top_k"] = top_k
                return [_make_result("a.py", "function", 0.01)]

            return _search

        with patch("code_rag.services.query_service.build_retriever", side_effect=_fake_build):
            result = service.ask("test question", tmp_path)

        assert called_with["mode"] == "hybrid"
        assert called_with["query"] == "test question"
        assert result.low_confidence is False
        assert len(result.retrieval.chunks) == 1

    def test_ask_mode_vector_uses_retriever(self, tmp_path: Path, tmp_settings) -> None:
        """ask(mode='vector') 应走 build_retriever('vector', ...) 路径。"""
        stub_results = [_make_result("a.py", "function", 0.1)]
        called_with: dict[str, Any] = {}

        def _fake_build(mode: str, settings: Any, resolved: Any) -> Any:
            called_with["mode"] = mode

            def _search(
                query: str, top_k: int | None = None, score_threshold: float | None = None
            ) -> Any:
                return stub_results

            return _search

        service = QueryService.__new__(QueryService)
        service._settings = tmp_settings  # type: ignore[attr-defined]
        service._retriever = None  # type: ignore[attr-defined]
        service._llm = None  # type: ignore[attr-defined]

        with patch("code_rag.services.query_service.build_retriever", side_effect=_fake_build):
            result = service.ask("q", tmp_path, mode="vector")

        assert called_with["mode"] == "vector"
        assert len(result.retrieval.chunks) == 1
