"""词法检索器 — 基于符号/文件名的关键词召回。

与向量检索互补：当查询里直接出现 ``cli.py``、``Retriever`` 等
标识符时，词法检索可以零成本地把相关 chunk 拉回到前列。

实现思路：

- 对 collection 内的所有 chunk 做一次扫描，按 ``file_path``、``name``、
  ``parent``、``source`` 构造倒排文本。
- 检索时用大小写不敏感的子串匹配 + 简单 TF 排序，
  返回 :class:`SearchResult` 列表，``score`` 越大越相关
  （与向量检索的"越小越相关"语义不同，会在 rerank 中归一化）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from code_rag.config import Settings, get_settings
from code_rag.repository import ResolvedRepo
from code_rag.store.vector_store import ChromaStore, SearchResult

logger = logging.getLogger(__name__)

LexicalRepo = str | Path | ResolvedRepo


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class _LexicalHit:
    """词法召回中间结果。"""

    chunk_id: str
    file_path: str
    name: str
    parent: str | None
    language: str
    chunk_type: str
    start_line: int
    end_line: int
    source: str
    file_hash: str
    score: float
    """词法匹配分数，越大越相关。"""
    matched_keywords: list[str]


# ---------------------------------------------------------------------------
# 词法检索器
# ---------------------------------------------------------------------------


class LexicalRetriever:
    """基于关键词的词法检索器。

    工作流程：

    1. 从 ChromaDB collection 中拉取全部 chunk 的 metadata + source
    2. 对每个 chunk 拼接 ``file_path``、``name``、``parent``、``source`` 作为可搜索文本
    3. 对查询做关键词提取（标识符、文件名、中文连续 2 字组）
    4. 命中的 chunk 按命中次数和位置权重计算 TF-style 分数
    5. 截取 ``top_k`` 条返回

    Args:
        store: ChromaStore 实例。
        repo_path: 仓库路径，用于确定 collection。
        settings: 应用配置；为 ``None`` 时使用默认配置。
    """

    def __init__(
        self,
        store: ChromaStore,
        repo_path: LexicalRepo,
        settings: Settings | None = None,
    ) -> None:
        """初始化词法检索器。"""
        self._store = store
        self._settings = settings or get_settings()
        if isinstance(repo_path, ResolvedRepo):
            self._repo_path = repo_path.root_path
            self._collection_name = ChromaStore.get_collection_name_from_key(
                repo_path.identity.collection_key
            )
        else:
            self._repo_path = Path(repo_path).resolve()
            self._collection_name = ChromaStore.get_collection_name(self._repo_path)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        top_k: int = 8,
    ) -> list[SearchResult]:
        """执行词法检索。

        Args:
            query: 用户查询。
            top_k: 返回的最大结果数。

        Returns:
            按词法分数降序排列的 :class:`SearchResult` 列表。
            ``score`` 字段为词法分数（越大越相关），
            调用方应在 rerank 阶段做归一化。
        """
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        try:
            collection = self._store._client.get_collection(name=self._collection_name)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning("词法检索失败，collection 不存在: %s", exc)
            return []

        total = collection.count()
        if total == 0:
            return []

        # 拉取 metadata + documents
        try:
            data = collection.get(limit=total, include=["metadatas", "documents"])
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning("词法检索拉取 collection 失败: %s", exc)
            return []

        ids = data.get("ids", [])
        metas = data.get("metadatas", [])
        docs = data.get("documents", [])

        hits: list[_LexicalHit] = []
        for chunk_id, meta, source in zip(ids, metas, docs):
            file_path = (meta or {}).get("file_path", "")
            name = (meta or {}).get("name", "")
            parent = (meta or {}).get("parent") or ""
            language = (meta or {}).get("language", "")
            chunk_type = (meta or {}).get("chunk_type", "")
            start_line = int((meta or {}).get("start_line", 0))
            end_line = int((meta or {}).get("end_line", 0))
            file_hash = (meta or {}).get("file_hash", "")

            score, matched = self._score_chunk(
                file_path=file_path,
                name=name,
                parent=parent,
                language=language,
                source=source or "",
                keywords=keywords,
            )
            if score <= 0:
                continue

            hits.append(
                _LexicalHit(
                    chunk_id=chunk_id,
                    file_path=file_path,
                    name=name,
                    parent=parent or None,
                    language=language,
                    chunk_type=chunk_type,
                    start_line=start_line,
                    end_line=end_line,
                    source=source or "",
                    file_hash=file_hash,
                    score=score,
                    matched_keywords=matched,
                )
            )

        # 按分数降序
        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[:top_k]

        # 重建 SearchResult
        from code_rag.indexer.chunker import CodeChunk

        results: list[SearchResult] = []
        for hit in top:
            chunk = CodeChunk(
                file_path=hit.file_path,
                language=hit.language,
                chunk_type=hit.chunk_type,
                name=hit.name,
                start_line=hit.start_line,
                end_line=hit.end_line,
                parent=hit.parent,
                file_hash=hit.file_hash,
                source=hit.source,
                token_count=0,
            )
            results.append(SearchResult(chunk=chunk, score=hit.score))

        logger.info("词法检索: query='%s' 命中 %d / %d", query[:50], len(results), total)
        return results

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_keywords(query: str) -> list[str]:
        """从查询中提取候选关键词。

        规则：
        - camelCase / snake_case / kebab-case 标识符（>=2 字符）
        - 文件名（xxx.yyy）
        - 中文连续 2 字组
        - 过滤常见中文停用词
        """
        if not query:
            return []
        keywords: list[str] = []
        # 标识符
        keywords.extend(re.findall(r"[a-zA-Z_][a-zA-Z0-9_-]{1,}", query))
        # 文件名
        keywords.extend(re.findall(r"[a-zA-Z0-9_-]+\.[a-zA-Z]{1,10}", query))
        # 中文 2 字组
        keywords.extend(re.findall(r"[一-鿿]{2,2}", query))
        # 过滤
        out: list[str] = []
        seen: set[str] = set()
        for kw in keywords:
            lower = kw.lower()
            if lower in seen or len(lower) < 2:
                continue
            if lower in _STOP_WORDS:
                continue
            seen.add(lower)
            out.append(lower)
        return out

    @staticmethod
    def _score_chunk(
        *,
        file_path: str,
        name: str,
        parent: str,
        language: str,
        source: str,
        keywords: list[str],
    ) -> tuple[float, list[str]]:
        """对单个 chunk 计算词法分数。"""
        matched: list[str] = []
        score = 0.0

        for kw in keywords:
            kw_lower = kw.lower()
            # 不同字段不同权重
            in_path = kw_lower in file_path.lower()
            in_name = kw_lower in name.lower()
            in_parent = parent and kw_lower in parent.lower()
            in_source = kw_lower in source.lower()
            in_language = kw_lower in language.lower()

            if in_path:
                score += 5.0
                matched.append(kw)
            if in_name:
                score += 4.0
                matched.append(kw)
            if in_parent:
                score += 2.0
                matched.append(kw)
            if in_source:
                # 源码中出现：统计出现次数（最多 5 次）
                count = source.lower().count(kw_lower)
                score += min(count, 5) * 0.5
                matched.append(kw)
            if in_language:
                score += 0.5

        # 去重
        matched = list(dict.fromkeys(matched))
        return score, matched


# 词法检索停用词（与 retriever.boost 共享子集）
_STOP_WORDS: set[str] = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "code",
    "file",
    "function",
    "class",
    "method",
    "module",
    "where",
    "what",
    "which",
    "how",
    "中文",
    "哪里",
    "什么",
    "怎么",
    "如何",
    "哪些",
    "哪个",
    "这个",
    "那个",
    "一下",
    "告诉",
    "请",
    "帮",
    "看",
    "说",
}
