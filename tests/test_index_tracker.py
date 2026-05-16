"""index_tracker 模块测试。

覆盖：
- added / modified / deleted 识别正确
- update_tracker 后无变更不重复索引
- 多次迭代的变更追踪

注意：使用 _make_entry 构造 FileEntry 而非 RepoScanner，
避免 pytest 在 tmp_path 中创建的元数据文件干扰。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from code_rag.indexer.scanner import FileEntry
from code_rag.store.index_tracker import IndexTracker

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _create_file(path: Path, content: str = "# placeholder") -> None:
    """在指定路径创建文件，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_entry(
    tmp_path: Path,
    rel: str,
    content: str,
    *,
    language: str = "python",
) -> FileEntry:
    """快速构造 FileEntry（用于不含扫描的单元测试）。"""
    abs_path = tmp_path / rel
    _create_file(abs_path, content)
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return FileEntry(
        abs_path=abs_path,
        rel_path=rel.replace("\\", "/"),
        language=language,
        extension=Path(rel).suffix.lower(),
        size=len(content.encode("utf-8")),
        file_hash=h,
    )


def _make_tracker(tmp_path: Path) -> IndexTracker:
    """创建指向临时目录的 tracker。"""
    tracker = IndexTracker.__new__(IndexTracker)
    tracker._settings = SimpleNamespace(
        index_tracker_path=tmp_path / "indexes",
    )
    return tracker


# ---------------------------------------------------------------------------
# 首次索引：全部为 added
# ---------------------------------------------------------------------------


class TestFirstIndex:
    """测试首次索引（无历史记录）时所有文件标记为 added。"""

    def test_all_files_added(self, tmp_path: Path) -> None:
        entries = [
            _make_entry(tmp_path, "a.py", "x = 1"),
            _make_entry(tmp_path, "b.py", "y = 2"),
        ]
        tracker = _make_tracker(tmp_path)

        changes = tracker.get_changes(tmp_path, entries)
        assert len(changes.added) == 2
        assert len(changes.modified) == 0
        assert len(changes.deleted) == 0
        assert changes.has_changes is True
        assert changes.total == 2


# ---------------------------------------------------------------------------
# 增量更新：modified / deleted
# ---------------------------------------------------------------------------


class TestIncrementalChanges:
    """测试增量更新时的 modified / deleted 识别。"""

    def test_modified_detected(self, tmp_path: Path) -> None:
        """修改文件后能被识别为 modified。"""
        entries1 = [_make_entry(tmp_path, "a.py", "x = 1")]
        tracker = _make_tracker(tmp_path)
        tracker.update_tracker(tmp_path, entries1)

        # 修改文件内容
        entries2 = [_make_entry(tmp_path, "a.py", "x = 999")]
        changes = tracker.get_changes(tmp_path, entries2)
        assert len(changes.added) == 0
        assert len(changes.modified) == 1
        assert len(changes.deleted) == 0

    def test_deleted_detected(self, tmp_path: Path) -> None:
        """删除文件后能被识别为 deleted。"""
        entries1 = [
            _make_entry(tmp_path, "a.py", "x = 1"),
            _make_entry(tmp_path, "b.py", "y = 2"),
        ]
        tracker = _make_tracker(tmp_path)
        tracker.update_tracker(tmp_path, entries1)

        # 只传入 a.py，模拟 b.py 被删除
        entries2 = [_make_entry(tmp_path, "a.py", "x = 1")]
        changes = tracker.get_changes(tmp_path, entries2)
        assert len(changes.deleted) == 1
        assert changes.deleted[0].rel_path == "b.py"

    def test_no_changes_after_update(self, tmp_path: Path) -> None:
        """update_tracker 后再次传入相同 entries，无变更应返回空 ChangeSet。"""
        entries1 = [
            _make_entry(tmp_path, "a.py", "x = 1"),
            _make_entry(tmp_path, "b.py", "y = 2"),
        ]
        tracker = _make_tracker(tmp_path)
        tracker.update_tracker(tmp_path, entries1)

        # 相同 entries（文件内容不变）
        entries2 = [
            _make_entry(tmp_path, "a.py", "x = 1"),
            _make_entry(tmp_path, "b.py", "y = 2"),
        ]
        changes = tracker.get_changes(tmp_path, entries2)
        assert changes.has_changes is False
        assert changes.total == 0

    def test_add_new_file(self, tmp_path: Path) -> None:
        """新增文件被识别为 added。"""
        entries1 = [_make_entry(tmp_path, "a.py", "x = 1")]
        tracker = _make_tracker(tmp_path)
        tracker.update_tracker(tmp_path, entries1)

        # 新增 b.py
        entries2 = [
            _make_entry(tmp_path, "a.py", "x = 1"),
            _make_entry(tmp_path, "b.py", "y = 2"),
        ]
        changes = tracker.get_changes(tmp_path, entries2)
        assert len(changes.added) == 1
        assert changes.added[0].rel_path == "b.py"
        assert len(changes.modified) == 0
        assert len(changes.deleted) == 0

    def test_mixed_changes(self, tmp_path: Path) -> None:
        """同时有 added + modified + deleted。"""
        entries1 = [
            _make_entry(tmp_path, "keep.py", "a = 1"),
            _make_entry(tmp_path, "modify.py", "b = 2"),
            _make_entry(tmp_path, "delete.py", "c = 3"),
        ]
        tracker = _make_tracker(tmp_path)
        tracker.update_tracker(tmp_path, entries1)

        # keep 不变，modify 改内容，delete 消失，new 新增
        entries2 = [
            _make_entry(tmp_path, "keep.py", "a = 1"),
            _make_entry(tmp_path, "modify.py", "b = 999"),
            _make_entry(tmp_path, "new.py", "d = 4"),
        ]
        changes = tracker.get_changes(tmp_path, entries2)

        added_names = [e.rel_path for e in changes.added]
        modified_names = [e.rel_path for e in changes.modified]
        deleted_names = [e.rel_path for e in changes.deleted]

        assert "new.py" in added_names
        assert "modify.py" in modified_names
        assert "delete.py" in deleted_names


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------


class TestTrackerEdgeCases:
    """测试 tracker 的边界情况。"""

    def test_empty_entries(self, tmp_path: Path) -> None:
        """空文件列表与空历史记录对比，应无变更。"""
        tracker = _make_tracker(tmp_path)
        changes = tracker.get_changes(tmp_path, [])
        assert changes.has_changes is False

    def test_tracker_persists_across_instances(self, tmp_path: Path) -> None:
        """tracker 数据应持久化到磁盘，新实例能读取。"""
        entries1 = [_make_entry(tmp_path, "a.py", "x = 1")]
        tracker1 = _make_tracker(tmp_path)
        tracker1.update_tracker(tmp_path, entries1)

        # 新建 tracker 实例
        tracker2 = _make_tracker(tmp_path)
        entries2 = [_make_entry(tmp_path, "a.py", "x = 1")]
        changes = tracker2.get_changes(tmp_path, entries2)
        assert changes.has_changes is False

    def test_tracker_path_isolation(self, tmp_path: Path) -> None:
        """不同仓库的 tracker 数据互不干扰。"""
        entries_a = [_make_entry(tmp_path, "a.py", "x = 1")]
        entries_b = [_make_entry(tmp_path, "b.py", "y = 2")]

        tracker = _make_tracker(tmp_path)
        # 使用不同路径作为"不同仓库"
        repo_a = tmp_path / "repo_a"
        repo_b = tmp_path / "repo_b"
        repo_a.mkdir()
        repo_b.mkdir()

        tracker.update_tracker(repo_a, entries_a)
        tracker.update_tracker(repo_b, entries_b)

        # repo_a 应该没有变更
        changes_a = tracker.get_changes(repo_a, entries_a)
        assert changes_a.has_changes is False

        # repo_b 应该也没有变更
        changes_b = tracker.get_changes(repo_b, entries_b)
        assert changes_b.has_changes is False
