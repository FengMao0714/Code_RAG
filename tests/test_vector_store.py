"""vector_store 模块测试。

覆盖：
- 使用 fake embedding，不加载真实 bge 模型
- upsert / query / delete_by_files / delete_collection / get_stats
- collection 不存在时 get_stats / delete_collection 不崩溃
- 重复 upsert 的幂等性
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from code_rag.indexer.chunker import CodeChunk
from code_rag.store.vector_store import ChromaStore

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path):
    """创建指向临时目录的 Settings。"""
    return SimpleNamespace(
        chroma_persist_path=tmp_path / "chroma",
        retrieval_top_k=8,
        retrieval_score_threshold=0.7,
    )


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


def _fake_embedding(dim: int = 1024, seed: int = 0) -> list[float]:
    """生成确定性 fake embedding。"""
    import math

    vec = [float((seed + i) % 256) for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm > 0 else vec


# ---------------------------------------------------------------------------
# 基础 CRUD
# ---------------------------------------------------------------------------


class TestChromaStoreCRUD:
    """测试 ChromaStore 的基本 CRUD 操作。"""

    def test_upsert_and_query(self, tmp_path: Path) -> None:
        """upsert 后 query 能检索到结果。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-crud"

        chunks = [_make_chunk(source="def hello(): pass")]
        embeddings = [_fake_embedding(seed=1)]

        store.upsert_chunks(coll_name, chunks, embeddings)

        # 用相同向量查询
        results = store.query(coll_name, _fake_embedding(seed=1), top_k=5)
        assert len(results) == 1
        assert results[0].chunk.source == "def hello(): pass"
        assert results[0].chunk.file_path == "app.py"

    def test_upsert_multiple_chunks(self, tmp_path: Path) -> None:
        """批量 upsert 多个 chunk。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-multi"

        chunks = [
            _make_chunk(name="func_a", source="def a(): pass", start_line=1),
            _make_chunk(name="func_b", source="def b(): pass", start_line=5),
            _make_chunk(name="func_c", source="def c(): pass", start_line=10),
        ]
        embeddings = [_fake_embedding(seed=i) for i in range(3)]

        store.upsert_chunks(coll_name, chunks, embeddings)

        stats = store.get_stats(coll_name)
        assert stats["exists"] is True
        assert stats["total_chunks"] == 3

    def test_upsert_idempotent(self, tmp_path: Path) -> None:
        """重复 upsert 相同 chunk 不产生重复记录。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-idempotent"

        chunk = _make_chunk(source="def idempotent(): pass")
        embedding = [_fake_embedding(seed=42)]

        store.upsert_chunks(coll_name, [chunk], embedding)
        store.upsert_chunks(coll_name, [chunk], embedding)

        stats = store.get_stats(coll_name)
        assert stats["total_chunks"] == 1  # 不应重复

    def test_query_empty_collection(self, tmp_path: Path) -> None:
        """空 collection 查询返回空列表。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-empty"

        # 创建但不写入
        store.get_or_create_collection(coll_name)

        results = store.query(coll_name, _fake_embedding(), top_k=5)
        assert results == []

    def test_query_nonexistent_collection_no_side_effect(self, tmp_path: Path) -> None:
        """未索引 collection 调用 query 后不会创建空 collection。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-nonexistent"

        results = store.query(coll_name, _fake_embedding(), top_k=5)
        assert results == []

        # collection 不应被创建
        stats = store.get_stats(coll_name)
        assert stats["exists"] is False

    def test_query_with_distance_threshold(self, tmp_path: Path) -> None:
        """距离阈值过滤。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-threshold"

        chunks = [_make_chunk(source="target")]
        store.upsert_chunks(coll_name, chunks, [_fake_embedding(seed=100)])

        # 用相同向量查询，距离应接近 0
        results_same = store.query(coll_name, _fake_embedding(seed=100), top_k=5)
        assert len(results_same) == 1
        assert results_same[0].score < 0.01  # 几乎相同

        # 设置极小阈值，用不同向量查询
        results_filtered = store.query(
            coll_name,
            _fake_embedding(seed=200),
            top_k=5,
            max_distance=0.001,
        )
        # 不同向量的距离应超过阈值，被过滤
        assert len(results_filtered) == 0


# ---------------------------------------------------------------------------
# 删除操作
# ---------------------------------------------------------------------------


class TestChromaStoreDelete:
    """测试删除操作。"""

    def test_delete_by_files(self, tmp_path: Path) -> None:
        """delete_by_files 删除指定文件的所有 chunk。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-del-files"

        chunks = [
            _make_chunk(file_path="a.py", name="f1", source="a1", start_line=1),
            _make_chunk(file_path="a.py", name="f2", source="a2", start_line=5),
            _make_chunk(file_path="b.py", name="f3", source="b1", start_line=1),
        ]
        embeddings = [_fake_embedding(seed=i) for i in range(3)]
        store.upsert_chunks(coll_name, chunks, embeddings)

        # 删除 a.py 的所有 chunk
        store.delete_by_files(coll_name, ["a.py"])

        stats = store.get_stats(coll_name)
        assert stats["total_chunks"] == 1  # 只剩 b.py

    def test_delete_collection(self, tmp_path: Path) -> None:
        """delete_collection 删除整个 collection。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-del-coll"

        chunks = [_make_chunk(source="x")]
        store.upsert_chunks(coll_name, chunks, [_fake_embedding()])
        assert store.get_stats(coll_name)["exists"] is True

        store.delete_collection(coll_name)
        assert store.get_stats(coll_name)["exists"] is False

    def test_delete_nonexistent_collection_no_crash(self, tmp_path: Path) -> None:
        """删除不存在的 collection 不崩溃。"""
        store = ChromaStore(_make_settings(tmp_path))
        # 不应抛出异常
        store.delete_collection("nonexistent-collection-xyz")

    def test_delete_by_files_empty_list(self, tmp_path: Path) -> None:
        """空文件列表不做任何操作。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-del-empty"
        store.get_or_create_collection(coll_name)
        # 不应抛出异常
        store.delete_by_files(coll_name, [])


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestChromaStoreStats:
    """测试 collection 统计。"""

    def test_stats_exists(self, tmp_path: Path) -> None:
        """已存在的 collection 返回正确的统计。"""
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-stats"

        chunks = [
            _make_chunk(chunk_type="function", source="f1"),
            _make_chunk(chunk_type="class", source="c1"),
            _make_chunk(chunk_type="doc", source="d1"),
        ]
        embeddings = [_fake_embedding(seed=i) for i in range(3)]
        store.upsert_chunks(coll_name, chunks, embeddings)

        stats = store.get_stats(coll_name)
        assert stats["exists"] is True
        assert stats["total_chunks"] == 3
        assert "function" in stats["chunk_types"]
        assert "class" in stats["chunk_types"]
        assert "doc" in stats["chunk_types"]

    def test_stats_nonexistent_collection_no_crash(self, tmp_path: Path) -> None:
        """查询不存在的 collection 统计不崩溃，返回 exists=False。"""
        store = ChromaStore(_make_settings(tmp_path))
        stats = store.get_stats("nonexistent-collection-xyz")
        assert stats["exists"] is False
        assert stats["total_chunks"] == 0


# ---------------------------------------------------------------------------
# 集成：upsert + query + delete 完整流程
# ---------------------------------------------------------------------------


class TestChromaStoreIntegration:
    """测试 upsert → query → delete 完整流程。"""

    def test_full_lifecycle(self, tmp_path: Path) -> None:
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-lifecycle"

        # 1. 写入
        chunks = [
            _make_chunk(
                file_path="src/auth.py",
                name="login",
                source="def login(user, pwd): ...",
                start_line=10,
                end_line=20,
            ),
            _make_chunk(
                file_path="src/auth.py",
                name="logout",
                source="def logout(): ...",
                start_line=22,
                end_line=25,
            ),
        ]
        embeddings = [_fake_embedding(seed=1), _fake_embedding(seed=2)]
        store.upsert_chunks(coll_name, chunks, embeddings)

        # 2. 查询
        results = store.query(coll_name, _fake_embedding(seed=1), top_k=10)
        assert len(results) >= 1
        assert any(r.chunk.name == "login" for r in results)

        # 3. 按文件删除
        store.delete_by_files(coll_name, ["src/auth.py"])
        stats = store.get_stats(coll_name)
        assert stats["total_chunks"] == 0

    def test_search_result_structure(self, tmp_path: Path) -> None:
        """SearchResult 包含 chunk 和 score。"""
        from code_rag.store.vector_store import SearchResult

        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-result-struct"

        chunk = _make_chunk(source="def test(): pass")
        store.upsert_chunks(coll_name, [chunk], [_fake_embedding(seed=5)])

        results = store.query(coll_name, _fake_embedding(seed=5), top_k=1)
        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert isinstance(results[0].chunk, CodeChunk)
        assert isinstance(results[0].score, float)

    def test_extra_metadata_roundtrip(self, tmp_path: Path) -> None:
        store = ChromaStore(_make_settings(tmp_path))
        coll_name = "test-extra-metadata"

        chunk = _make_chunk(source="def huge(): pass")
        chunk.metadata["sub_index"] = 1
        chunk.metadata["sub_total"] = 3
        store.upsert_chunks(coll_name, [chunk], [_fake_embedding(seed=9)])

        results = store.query(coll_name, _fake_embedding(seed=9), top_k=1)
        assert len(results) == 1
        assert results[0].chunk.metadata == {"sub_index": 1, "sub_total": 3}
