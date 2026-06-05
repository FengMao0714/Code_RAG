"""Golden Query 数据集加载与数据类定义。

YAML 格式::

    - id: cli_entry
      question: CLI 入口在哪里？
      expected_files:
        - src/code_rag/cli.py
        - pyproject.toml
      expected_symbols:
        - app
      category: symbol_location
      difficulty: easy
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class GoldenQuery:
    """单条 golden query。

    Attributes:
        id: 唯一标识。
        question: 检索问题原文。
        expected_files: 期望命中的文件相对路径列表。
        expected_symbols: 期望命中的符号（函数/类/模块名）列表。
        category: 问题类别（自由文本，如 ``symbol_location``）。
        difficulty: 难度（easy / medium / hard）。
        mode: 检索模式覆盖（vector / lexical / hybrid），为空时使用默认。
        description: 额外说明。
    """

    id: str
    question: str
    expected_files: list[str] = field(default_factory=list)
    expected_symbols: list[str] = field(default_factory=list)
    category: str = ""
    difficulty: str = ""
    mode: str = ""
    description: str = ""


@dataclass
class GoldenDataset:
    """Golden query 数据集。"""

    name: str
    queries: list[GoldenQuery]

    def __len__(self) -> int:
        return len(self.queries)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.queries)


def load_dataset(path: str | Path) -> GoldenDataset:
    """加载 YAML 格式的 golden query 数据集。

    Args:
        path: YAML 文件路径。

    Returns:
        :class:`GoldenDataset`。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 文件格式错误。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"golden query 数据集不存在: {path}")

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - 防御
        raise RuntimeError("PyYAML 未安装。请执行: uv add pyyaml") from exc

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, list):
        raise ValueError(f"golden query 数据集根节点必须是列表: {path}")

    queries: list[GoldenQuery] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx + 1} 条不是字典: {item!r}")
        qid = str(item.get("id") or item.get("question") or f"q{idx + 1}")
        queries.append(
            GoldenQuery(
                id=qid,
                question=str(item.get("question", "")).strip(),
                expected_files=[str(p) for p in item.get("expected_files", [])],
                expected_symbols=[str(s) for s in item.get("expected_symbols", [])],
                category=str(item.get("category", "")),
                difficulty=str(item.get("difficulty", "")),
                mode=str(item.get("mode", "")),
                description=str(item.get("description", "")),
            )
        )

    logger.info("已加载 golden query 数据集: %s (%d 条)", path, len(queries))
    return GoldenDataset(name=path.stem, queries=queries)
