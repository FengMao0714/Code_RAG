"""共享测试 fixtures。

为所有测试模块提供通用的临时配置、fake Embedder、fake LLM 等。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from code_rag.config import Settings


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> Settings:
    """创建指向临时目录的 Settings，避免读写真实 .env 和持久化数据。"""
    return Settings(
        chroma_persist_dir=str(tmp_path / "chroma"),
        index_tracker_dir=str(tmp_path / "indexes"),
        repo_cache_dir=str(tmp_path / "repos"),
        llm_api_key="test-key-not-real",
        llm_base_url="http://localhost:9999/v1",
        llm_model="fake-model",
        embedding_model="fake-embedding",
        embedding_device="cpu",
    )


class FakeEmbedder:
    """确定性 fake Embedder，不加载任何真实模型。

    基于文本内容的 SHA-256 生成 1024 维归一化向量，
    确保相同文本得到相同向量，不同文本得到不同向量。
    """

    _DIM: int = 1024

    def embed_texts(self, texts: list[str], **_: Any) -> list[list[float]]:
        return [self._hash_embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._hash_embed(text)

    @classmethod
    def _hash_embed(cls, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [float(b) for b in digest]
        # 重复填充到目标维度
        while len(raw) < cls._DIM:
            raw.extend(raw)
        vec = raw[: cls._DIM]
        # L2 归一化
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm > 0 else vec


class FakeLLMClient:
    """确定性 fake LLM 客户端，不发起任何网络请求。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def generate(
        self,
        context: str,
        question: str,
        **_: Any,
    ) -> str:
        return f"[fake answer] context_length={len(context)}, question={question}"

    def generate_stream(
        self,
        context: str,
        question: str,
        **_: Any,
    ) -> Generator[Any, None, None]:
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class FakeChunk:
            content: str
            finish_reason: str | None
            chunk_index: int

        answer = f"[fake answer] context_length={len(context)}, question={question}"
        yield FakeChunk(content=answer, finish_reason="stop", chunk_index=0)

    def health_check(self) -> bool:
        return True


@pytest.fixture()
def fake_embedder() -> FakeEmbedder:
    """提供一个 FakeEmbedder 实例。"""
    return FakeEmbedder()


@pytest.fixture()
def patch_embedder(fake_embedder: FakeEmbedder):
    """Monkeypatch Embedder.get_instance 返回 FakeEmbedder。

    用法：在测试函数参数中声明 ``patch_embedder`` 即可自动生效。
    """
    with patch(
        "code_rag.indexer.embedder.Embedder.get_instance",
        return_value=fake_embedder,
    ):
        yield fake_embedder


@pytest.fixture()
def patch_llm():
    """Monkeypatch LLMClient 构造函数返回 FakeLLMClient。

    用法：在测试函数参数中声明 ``patch_llm`` 即可自动生效。
    """
    with patch("code_rag.cli.LLMClient", FakeLLMClient):
        yield
