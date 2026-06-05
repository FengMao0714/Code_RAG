"""远程 Git 仓库缓存目录管理。

缓存结构示例::

    {repo_cache_dir}/
      repo_a1b2c3d4/         # 缓存目录名（取 base 最后一段 + 8 位 hex 摘要）
        worktree/             # 实际可扫描的代码目录
        metadata.json         # 缓存元信息
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_WORKTREE_DIRNAME = "worktree"
_METADATA_FILENAME = "metadata.json"


def cache_dir_name_for(canonical_url: str) -> str:
    """根据 canonical URL 推导缓存目录名。

    规则：

    - 规范化后的 URL 经过去 scheme、``user@`` 前缀、``.git`` 后缀处理。
    - 路径分隔符、端口号冒号、域名点统一替换为下划线。
    - 为了避免 Windows 路径过长，**始终附加 8 位 hex 摘要**。
    - 例如 ``https://github.com/owner/repo.git`` → ``repo_a1b2c3d4``。

    Args:
        canonical_url: 规范化后的 URL。

    Returns:
        缓存目录名。
    """
    text = canonical_url.strip()
    # 去除 scheme 分隔符
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", text)
    # 去除 scp-like 形式前缀 ``user@``
    text = re.sub(r"^[^/@]+@", "", text)
    # 去除末尾 ``.git``
    if text.endswith(".git"):
        text = text[: -len(".git")]
    # 路径分隔符、端口号冒号、域名点统一为下划线
    text = text.replace(":", "_").replace("/", "_").replace("\\", "_").replace(".", "_")
    # 去除 ``_`` 重复
    text = re.sub(r"_+", "_", text).strip("_")
    # 8 位 hex 摘要：避免不同仓库偶然撞名 + 控制路径长度
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:8]
    base = text or "repo"
    # 取 base 最后一段（例如 github_com_owner_repo → repo），
    # 避免长 path + 长 hex 导致 Windows 路径超 260 字符
    parts = base.split("_")
    short = parts[-1] if parts else base
    return f"{short}_{digest}"


@dataclass
class CacheEntry:
    """单个缓存仓库的元信息。"""

    cache_dir: Path
    canonical_url: str
    ref: str | None
    commit: str | None
    cloned_at: str | None
    updated_at: str | None
    metadata: dict = field(default_factory=dict)

    @property
    def worktree(self) -> Path:
        """实际可扫描的 worktree 目录。"""
        return self.cache_dir / _WORKTREE_DIRNAME

    @property
    def exists(self) -> bool:
        """缓存目录与 worktree 是否都存在。"""
        return self.cache_dir.is_dir() and self.worktree.is_dir()

    def to_dict(self) -> dict:
        """转字典，便于序列化。"""
        return {
            "canonical_url": self.canonical_url,
            "ref": self.ref,
            "commit": self.commit,
            "cloned_at": self.cloned_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, cache_dir: Path, data: dict) -> CacheEntry:
        """从字典构造。"""
        return cls(
            cache_dir=cache_dir,
            canonical_url=str(data.get("canonical_url", "")),
            ref=data.get("ref"),
            commit=data.get("commit"),
            cloned_at=data.get("cloned_at"),
            updated_at=data.get("updated_at"),
            metadata=dict(data.get("metadata") or {}),
        )


class CacheManager:
    """远程仓库缓存目录管理器。

    Args:
        cache_root: 缓存根目录。
    """

    def __init__(self, cache_root: str | Path) -> None:
        """初始化缓存管理器。"""
        self._root = Path(cache_root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """缓存根目录绝对路径。"""
        return self._root

    def cache_dir_for(self, canonical_url: str) -> Path:
        """根据 canonical URL 返回缓存目录路径（不创建）。"""
        return self._root / cache_dir_name_for(canonical_url)

    def get(self, canonical_url: str) -> CacheEntry | None:
        """读取指定 URL 的缓存条目。

        找不到元信息或 worktree 时返回 ``None``。
        """
        cache_dir = self.cache_dir_for(canonical_url)
        if not cache_dir.is_dir():
            return None
        metadata_path = cache_dir / _METADATA_FILENAME
        if not metadata_path.is_file():
            return None
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("无法解析缓存元信息 %s: %s", metadata_path, exc)
            return None
        return CacheEntry.from_dict(cache_dir=cache_dir, data=data)

    def ensure_metadata(self, cache_dir: Path) -> dict:
        """读取或初始化 metadata.json。

        Args:
            cache_dir: 缓存目录路径。

        Returns:
            metadata 字典。
        """
        metadata_path = cache_dir / _METADATA_FILENAME
        if metadata_path.is_file():
            try:
                return json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("无法解析缓存 metadata: %s — %s", metadata_path, exc)
        return {
            "canonical_url": "",
            "ref": None,
            "commit": None,
            "cloned_at": None,
            "updated_at": None,
            "metadata": {},
        }

    def write_metadata(self, cache_dir: Path, data: dict) -> None:
        """写入 metadata.json。"""
        cache_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = cache_dir / _METADATA_FILENAME
        metadata_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.debug("已写入缓存 metadata: %s", metadata_path)

    def update_entry(
        self,
        cache_dir: Path,
        *,
        canonical_url: str,
        ref: str | None,
        commit: str | None,
        is_initial: bool,
        extra: dict | None = None,
    ) -> CacheEntry:
        """更新缓存元信息。

        Args:
            cache_dir: 缓存目录。
            canonical_url: canonical URL。
            ref: 当前的 ref。
            commit: 当前的 commit SHA。
            is_initial: 是否为首次 clone。
            extra: 额外 metadata。

        Returns:
            更新后的 :class:`CacheEntry`。
        """
        now = datetime.now().isoformat(timespec="seconds")
        data = self.ensure_metadata(cache_dir)
        data["canonical_url"] = canonical_url
        data["ref"] = ref
        data["commit"] = commit
        if is_initial or not data.get("cloned_at"):
            data["cloned_at"] = now
        data["updated_at"] = now
        if extra:
            data["metadata"] = {**(data.get("metadata") or {}), **extra}
        self.write_metadata(cache_dir, data)
        return CacheEntry.from_dict(cache_dir=cache_dir, data=data)

    def list_entries(self) -> list[CacheEntry]:
        """列出所有缓存条目。"""
        entries: list[CacheEntry] = []
        if not self._root.is_dir():
            return entries
        for child in sorted(self._root.iterdir()):
            if not child.is_dir():
                continue
            entry = self.get_from_dir(child)
            if entry is not None:
                entries.append(entry)
        return entries

    def get_from_dir(self, cache_dir: Path) -> CacheEntry | None:
        """从已知的 cache_dir 加载 :class:`CacheEntry`。

        与 :meth:`get` 的区别是不会再用 canonical URL 反查目录。
        """
        metadata_path = cache_dir / _METADATA_FILENAME
        if not metadata_path.is_file():
            return None
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("无法解析缓存元信息 %s: %s", metadata_path, exc)
            return None
        return CacheEntry.from_dict(cache_dir=cache_dir, data=data)

    def remove(self, canonical_url: str) -> bool:
        """删除指定 URL 对应的缓存目录。

        Returns:
            是否实际删除了缓存。
        """
        import shutil

        cache_dir = self.cache_dir_for(canonical_url)
        if not cache_dir.is_dir():
            return False
        shutil.rmtree(cache_dir)
        logger.info("已删除仓库缓存: %s", cache_dir)
        return True

    def prune(self) -> list[Path]:
        """删除所有缓存目录。

        Returns:
            被删除的缓存目录列表。
        """
        import shutil

        removed: list[Path] = []
        for child in list(self._root.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child)
        logger.info("已清理 %d 个远程仓库缓存目录", len(removed))
        return removed


def collection_key_for_git(canonical_url: str, ref: str | None) -> str:
    """为 git 仓库生成稳定 collection_key。

    同一 canonical URL + 同一 ref 永远生成同样的 key。
    """
    text = canonical_url.strip()
    if text.endswith(".git"):
        text = text[: -len(".git")]
    ref_part = (ref or "DEFAULT").strip().replace("/", "_") or "DEFAULT"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"git-{digest}-{ref_part}"


def collection_key_for_local(abs_path: str) -> str:
    """为本地路径生成稳定 collection_key。

    为保持向后兼容，**返回与老 :meth:`ChromaStore.get_collection_name` 相同的
    12 位十六进制串**。这样 ``get_collection_name_from_key`` 输出的 collection
    名称与老逻辑完全一致，老的本地索引不会失效。
    """
    return hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:12]
