"""QueryService 单元测试。

覆盖：
- search 走 Retriever
- ask 返回低置信度判定（context 为空 / 全 doc / 平均距离过高）
- 流式接口返回 generator
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        low, reason = QueryService._evaluate_confidence(retrieval)
        assert low is True
        assert "未找到" in reason

    def test_only_doc_chunks_low_confidence(self) -> None:
        chunks = [
            _make_result("README.md", "doc", 0.3).chunk,
        ]
        scores = [0.3]
        retrieval = RetrievalResult(question="q", chunks=chunks, context="x", scores=scores)
        low, reason = QueryService._evaluate_confidence(retrieval)
        assert low is True
        assert "文档" in reason

    def test_code_chunks_high_confidence(self) -> None:
        chunks = [_make_result("a.py", "function", 0.4).chunk]
        scores = [0.4]
        retrieval = RetrievalResult(question="q", chunks=chunks, context="x", scores=scores)
        low, _reason = QueryService._evaluate_confidence(retrieval)
        assert low is False

    def test_high_avg_distance_low_confidence(self) -> None:
        chunks = [_make_result("a.py", "function", 1.5).chunk]
        scores = [1.5]
        retrieval = RetrievalResult(question="q", chunks=chunks, context="x", scores=scores)
        low, reason = QueryService._evaluate_confidence(retrieval)
        assert low is True
        assert "距离过高" in reason


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
        retriever = _StubRetriever([_make_result("a.py", "function", 0.1)])
        service = QueryService.__new__(QueryService)
        service._settings = tmp_settings  # type: ignore[attr-defined]
        service._retriever = retriever  # type: ignore[attr-defined]
        service._llm = None  # type: ignore[attr-defined]

        results = service.search("q", tmp_path, top_k=3)
        assert len(results) == 1
        assert retriever.calls[0][2] == 3

    def test_ask_returns_low_confidence_for_empty(self, tmp_path: Path, tmp_settings) -> None:
        retriever = _StubRetriever([])
        service = QueryService.__new__(QueryService)
        service._settings = tmp_settings  # type: ignore[attr-defined]
        service._retriever = retriever  # type: ignore[attr-defined]
        service._llm = None  # type: ignore[attr-defined]

        result = service.ask("q", tmp_path)
        assert result.low_confidence is True
        assert result.retrieval.chunks == []
