"""IndexService 单元测试。

覆盖：
- 空仓库索引（仅生成 module_summary）
- 二次索引无变更时 ``had_changes=False``
- 完整链路产出 chunks
- 进度回调被调用
- 路径不存在时抛错
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_rag.indexer.chunker import CodeChunk
from code_rag.services import IndexService


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def hi():\n    return 1\n", encoding="utf-8")
    (repo / "README.md").write_text("# Title\ndocs", encoding="utf-8")
    return repo


def _make_chunk(file_path: str, name: str, source: str = "def x(): pass") -> CodeChunk:
    return CodeChunk(
        file_path=file_path,
        language="python",
        chunk_type="function",
        name=name,
        start_line=1,
        end_line=1,
        parent=None,
        file_hash="x",
        source=source,
        token_count=5,
    )


class TestIndexService:
    def test_indexes_repo(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        repo = _make_repo(tmp_path)
        service = IndexService(tmp_settings)

        calls: list[tuple[str, str]] = []

        def cb(stage: str, message: str) -> None:
            calls.append((stage, message))

        result = service.run_index(repo, progress=cb)

        assert result.had_changes is True
        assert result.added + result.modified > 0
        assert result.chunks_generated > 0
        # 进度回调至少出现 scan / detect / embed / upsert
        stages = {stage for stage, _ in calls}
        assert "scan" in stages
        assert "detect" in stages
        assert "embed" in stages
        assert "upsert" in stages

    def test_second_index_no_changes(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        repo = _make_repo(tmp_path)
        service = IndexService(tmp_settings)

        first = service.run_index(repo)
        assert first.had_changes is True

        second = service.run_index(repo)
        assert second.had_changes is False
        assert second.chunks_generated == 0

    def test_missing_path_raises(self, tmp_path: Path, tmp_settings) -> None:
        service = IndexService(tmp_settings)
        with pytest.raises(FileNotFoundError):
            service.run_index(tmp_path / "nope")

    def test_empty_repo(self, tmp_path: Path, tmp_settings, patch_embedder) -> None:
        repo = tmp_path / "empty"
        repo.mkdir()
        service = IndexService(tmp_settings)
        result = service.run_index(repo)
        assert result.scanned_files == 0
        assert result.chunks_generated == 0

    def test_modified_file_old_chunks_cleaned(
        self, tmp_path: Path, tmp_settings, patch_embedder
    ) -> None:
        """修改文件后，旧内容的 chunk 应被清除，不应翻倍。"""
        from code_rag.store.vector_store import ChromaStore

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("def old_func():\n    return 1\n", encoding="utf-8")

        service = IndexService(tmp_settings)
        first = service.run_index(repo)
        assert first.had_changes is True
        first_chunk_count = first.chunks_generated
        assert first_chunk_count > 0

        # 修改 a.py
        (repo / "a.py").write_text("def new_func():\n    return 2\n", encoding="utf-8")

        second = service.run_index(repo)
        assert second.had_changes is True

        # chunk 数不应翻倍（旧 chunk 已被清理）
        assert second.chunks_generated <= first_chunk_count

        # 旧源码不应可检索
        store = ChromaStore(tmp_settings)
        coll_name = first.collection_name
        stats = store.get_stats(coll_name)
        assert stats["exists"] is True
        # 总 chunk 数应等于第二次索引的 chunks（旧的已删除）
        assert stats["total_chunks"] == second.chunks_generated

    def test_non_baseline_profile_uses_isolated_collection_and_manifest(
        self, tmp_path: Path, tmp_settings, patch_embedder
    ) -> None:
        from code_rag.config import Settings
        from code_rag.services import ManifestService

        repo = _make_repo(tmp_path)
        settings = Settings(
            chroma_persist_dir=str(tmp_path / "chroma"),
            index_tracker_dir=str(tmp_path / "indexes"),
            repo_cache_dir=str(tmp_path / "repos"),
            llm_api_key="test-key-not-real",
            llm_base_url="http://localhost:9999/v1",
            llm_model="fake-model",
            embedding_profile="bge-m3",
        )
        service = IndexService(settings)

        result = service.run_index(repo)
        manifest, _stats = ManifestService(settings).get_status(repo)

        assert result.collection_key.endswith("__emb_bge-m3")
        assert result.collection_name != tmp_settings.embedding_model
        assert manifest is not None
        assert manifest.embedding_profile == "bge-m3"
        assert manifest.embedding_model == "BAAI/bge-m3"
        assert "cross-lingual" in manifest.embedding_profile_rationale.lower()
