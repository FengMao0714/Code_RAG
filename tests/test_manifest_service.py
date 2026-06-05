"""ManifestService 单元测试。

覆盖：
- update_manifest 写入 JSON
- get_manifest 读取已写入数据
- list_manifests 按时间倒序
- remove_manifest 删除文件
- get_status 同时返回 manifest 和 store stats
"""

from __future__ import annotations

from pathlib import Path

from code_rag.services import ManifestService


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    return repo


class TestManifestService:
    def test_update_and_get(self, tmp_path: Path, tmp_settings) -> None:
        repo = _make_repo(tmp_path)
        service = ManifestService(tmp_settings)
        entry = service.update_manifest(
            repo, file_count=3, chunk_count=10, chunk_types={"function": 7, "doc": 3}
        )
        assert entry.repo_path == str(repo.resolve())
        assert entry.file_count == 3
        assert entry.chunk_count == 10
        assert entry.chunk_types == {"function": 7, "doc": 3}
        assert entry.embedding_model == tmp_settings.embedding_model

        loaded = service.get_manifest(repo)
        assert loaded is not None
        assert loaded.repo_path == entry.repo_path
        assert loaded.chunk_count == 10

    def test_list_empty(self, tmp_path: Path, tmp_settings) -> None:
        service = ManifestService(tmp_settings)
        assert service.list_manifests() == []

    def test_list_ordered(self, tmp_path: Path, tmp_settings) -> None:
        import time

        a = _make_repo(tmp_path / "a")
        b = _make_repo(tmp_path / "b")
        service = ManifestService(tmp_settings)
        service.update_manifest(a, file_count=1, chunk_count=1)
        time.sleep(1.1)  # 确保 last_indexed_at 不同（秒级精度）
        service.update_manifest(b, file_count=2, chunk_count=2)

        entries = service.list_manifests()
        assert len(entries) == 2
        # last_indexed_at desc: b 后写入
        assert entries[0].repo_path == str(b.resolve())
        assert entries[1].repo_path == str(a.resolve())

    def test_remove_manifest(self, tmp_path: Path, tmp_settings) -> None:
        repo = _make_repo(tmp_path)
        service = ManifestService(tmp_settings)
        service.update_manifest(repo, file_count=1, chunk_count=1)

        assert service.remove_manifest(repo) is True
        # 第二次删除返回 False
        assert service.remove_manifest(repo) is False
        assert service.get_manifest(repo) is None

    def test_get_status(self, tmp_path: Path, tmp_settings) -> None:
        repo = _make_repo(tmp_path)
        service = ManifestService(tmp_settings)
        service.update_manifest(repo, file_count=1, chunk_count=0)

        manifest, stats = service.get_status(repo)
        assert manifest is not None
        # stats from ChromaStore; collection doesn't exist
        assert stats.get("exists") is False
