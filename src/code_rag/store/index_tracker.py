"""增量索引追踪模块。

通过 SHA-256 哈希对比仓库文件的变更状态（added / modified / deleted），
实现仅对变化文件重新索引的增量更新策略。

追踪数据以 JSON 格式存储在 ``config.index_tracker_path`` 下，
每个仓库对应独立的 ``tracker.json`` 文件。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from code_rag.config import Settings, get_settings
from code_rag.indexer.scanner import FileEntry

logger = logging.getLogger(__name__)

# tracker 文件名
_TRACKER_FILENAME = "tracker.json"


# ---------------------------------------------------------------------------
# 变更结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeSet:
    """仓库文件变更集合。

    由 :meth:`IndexTracker.get_changes` 返回，
    描述自上次索引以来文件的新增、修改、删除情况。

    Attributes:
        added: 本次新增的文件条目。
        modified: 内容发生变化的文件条目。
        deleted: 已被删除的文件条目（仅含路径和哈希等基础信息）。
    """

    added: list[FileEntry] = field(default_factory=list)
    modified: list[FileEntry] = field(default_factory=list)
    deleted: list[FileEntry] = field(default_factory=list)

    @property
    def total(self) -> int:
        """变更文件总数。"""
        return len(self.added) + len(self.modified) + len(self.deleted)

    @property
    def has_changes(self) -> bool:
        """是否存在任何变更。"""
        return self.total > 0


# ---------------------------------------------------------------------------
# 索引追踪器
# ---------------------------------------------------------------------------


class IndexTracker:
    """增量索引追踪器。

    通过持久化文件哈希记录，对比仓库文件的变更状态，
    支持仅对变化文件执行重新索引。

    存储路径：``{index_tracker_path}/{repo_hash}/tracker.json``

    用法::

        tracker = IndexTracker()
        changes = tracker.get_changes(repo_path, current_hashes)
        if changes.has_changes:
            # 对 added + modified 文件执行索引 ...
            tracker.update_tracker(repo_path, current_file_entries)

    Args:
        settings: 应用配置；为 ``None`` 时使用默认配置。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化追踪器。

        Args:
            settings: 应用配置。
        """
        self._settings = settings or get_settings()

    def get_changes(
        self,
        repo_path: str | Path,
        current_file_entries: list[FileEntry],
    ) -> ChangeSet:
        """对比当前扫描结果与已存储记录，计算文件变更。

        将当前仓库的文件列表（来自 :class:`RepoScanner`）与上次索引
        记录的 SHA-256 哈希进行比对，将文件分为 added / modified / deleted。

        Args:
            repo_path: 仓库路径。
            current_file_entries: 当前扫描产出的文件条目列表。

        Returns:
            :class:`ChangeSet` 实例，包含 added、modified、deleted 列表。
        """
        current_lookup: dict[str, FileEntry] = {
            entry.rel_path: entry for entry in current_file_entries
        }
        current_hashes: dict[str, str] = {
            entry.rel_path: entry.file_hash for entry in current_file_entries
        }
        stored_hashes = self._load(repo_path)

        added: list[FileEntry] = []
        modified: list[FileEntry] = []
        deleted: list[FileEntry] = []

        # 遍历当前文件：识别 added / modified
        for rel_path, current_hash in current_hashes.items():
            if rel_path not in stored_hashes:
                added.append(current_lookup[rel_path])
            elif stored_hashes[rel_path] != current_hash:
                modified.append(current_lookup[rel_path])

        # 遍历已存储文件：识别 deleted
        for rel_path in stored_hashes:
            if rel_path not in current_hashes:
                deleted.append(
                    FileEntry(
                        abs_path=Path(repo_path) / rel_path,
                        rel_path=rel_path,
                        language=None,
                        extension=Path(rel_path).suffix.lower(),
                        size=0,
                        file_hash=stored_hashes[rel_path],
                    )
                )

        logger.info(
            "变更检测完成：%d 新增, %d 修改, %d 删除",
            len(added),
            len(modified),
            len(deleted),
        )
        return ChangeSet(added=added, modified=modified, deleted=deleted)

    def update_tracker(
        self,
        repo_path: str | Path,
        file_entries: list[FileEntry],
    ) -> None:
        """持久化当前文件哈希记录。

        索引完成后调用，将最新文件哈希写入 tracker.json，
        替换上一次的记录。

        Args:
            repo_path: 仓库路径。
            file_entries: 本次索引后的文件条目列表。
        """
        hashes = {entry.rel_path: entry.file_hash for entry in file_entries}
        self._save(repo_path, hashes)
        logger.info("已持久化 %d 条追踪记录", len(hashes))

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_tracker_path(self, repo_path: str | Path) -> Path:
        """获取仓库对应的 tracker.json 文件路径。

        Args:
            repo_path: 仓库路径。

        Returns:
            ``{index_tracker_path}/{repo_hash}/tracker.json``。
        """
        repo_hash = self._hash_repo_path(repo_path)
        return self._settings.index_tracker_path / repo_hash / _TRACKER_FILENAME

    def _load(self, repo_path: str | Path) -> dict[str, str]:
        """从磁盘加载仓库的文件哈希记录。

        如果 tracker 文件不存在（首次索引），返回空字典。

        Args:
            repo_path: 仓库路径。

        Returns:
            ``{rel_path: sha256}`` 映射。
        """
        path = self._get_tracker_path(repo_path)
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            logger.debug("已加载追踪记录: %s (%d 条)", path, len(data))
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("追踪文件读取失败，视为首次索引: %s — %s", path, exc)
            return {}

    def _save(self, repo_path: str | Path, hashes: dict[str, str]) -> None:
        """将文件哈希记录写入磁盘。

        自动创建父目录。写入前加载已有记录并合并，
        确保不会丢失未传入的文件记录。

        Args:
            repo_path: 仓库路径。
            hashes: ``{rel_path: sha256}`` 映射。
        """
        path = self._get_tracker_path(repo_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(hashes, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.debug("已保存追踪记录: %s (%d 条)", path, len(hashes))

    @staticmethod
    def _hash_repo_path(repo_path: str | Path) -> str:
        """将仓库路径转换为唯一的目录名。

        使用绝对路径的 SHA-256 哈希前 12 位，
        与 :meth:`ChromaStore.get_collection_name` 保持一致。

        Args:
            repo_path: 仓库路径。

        Returns:
            12 位十六进制字符串。
        """
        abs_path = str(Path(repo_path).resolve())
        return hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:12]
