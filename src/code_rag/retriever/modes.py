"""Shared retrieval mode validation."""

from __future__ import annotations

from typing import ClassVar


class SearchMode:
    """Constants and helpers for retrieval modes."""

    VECTOR: ClassVar[str] = "vector"
    LEXICAL: ClassVar[str] = "lexical"
    HYBRID: ClassVar[str] = "hybrid"
    DEFAULT: ClassVar[str] = HYBRID
    SUPPORTED: ClassVar[tuple[str, ...]] = (VECTOR, LEXICAL, HYBRID)

    @classmethod
    def normalize(cls, mode: str) -> str:
        """Return a canonical retrieval mode or raise ``ValueError``."""
        normalized = (mode or "").strip().lower()
        if normalized not in cls.SUPPORTED:
            raise ValueError(f"不支持的检索模式: {mode}（应为 vector/lexical/hybrid）")
        return normalized

    @classmethod
    def parse_csv(cls, modes: str) -> list[str]:
        """Parse comma-separated mode names while preserving order."""
        parsed: list[str] = []
        for item in modes.split(","):
            normalized = cls.normalize(item)
            if normalized not in parsed:
                parsed.append(normalized)
        if not parsed:
            raise ValueError("compare modes 不能为空")
        return parsed
