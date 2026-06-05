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
