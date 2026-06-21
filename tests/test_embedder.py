"""Embedder loading policy tests."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from code_rag.config import Settings
from code_rag.indexer.embedder import Embedder


class FakeSentenceTransformer:
    """Small fake for asserting SentenceTransformer constructor options."""

    calls: list[dict[str, Any]] = []
    encoded_batches: list[list[str]] = []
    fail_local: bool = False

    def __init__(self, model_name: str, *, device: str, **kwargs: Any) -> None:
        self.model_name = model_name
        self.device = device
        self.kwargs = kwargs
        type(self).calls.append({"model_name": model_name, "device": device, **kwargs})
        if kwargs.get("local_files_only") and type(self).fail_local:
            raise OSError("cache miss")

    def encode(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        texts = list(_args[0]) if _args else []
        type(self).encoded_batches.append(texts)
        return [SimpleNamespace(tolist=lambda: [0.0])]


@pytest.fixture(autouse=True)
def reset_fake(monkeypatch: pytest.MonkeyPatch):
    """Install the fake sentence_transformers module for each test."""
    hf_env_keys = (
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_DATASETS_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
    )
    old_env = {key: os.environ.get(key) for key in hf_env_keys}
    FakeSentenceTransformer.calls = []
    FakeSentenceTransformer.encoded_batches = []
    FakeSentenceTransformer.fail_local = False
    Embedder.reset()
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setattr(Embedder, "_resolve_local_model_path", lambda _self: "cached-model")
    yield
    Embedder.reset()
    for key, value in old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _settings(**kwargs: Any) -> Settings:
    return Settings(
        llm_api_key="x",
        llm_base_url="http://x",
        llm_model="m",
        embedding_model="BAAI/bge-large-zh-v1.5",
        **kwargs,
    )


def test_load_model_uses_local_cache_first() -> None:
    embedder = Embedder(_settings())

    embedder._load_model()

    assert len(FakeSentenceTransformer.calls) == 1
    assert FakeSentenceTransformer.calls[0]["local_files_only"] is True
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_load_model_falls_back_online_when_cache_missing() -> None:
    for key in (
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_DATASETS_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
    ):
        os.environ.pop(key, None)
    FakeSentenceTransformer.fail_local = True
    embedder = Embedder(_settings())

    embedder._load_model()

    assert [call["local_files_only"] for call in FakeSentenceTransformer.calls] == [True, False]
    assert os.environ.get("HF_HUB_OFFLINE") is None


def test_load_model_offline_raises_when_cache_missing() -> None:
    FakeSentenceTransformer.fail_local = True
    embedder = Embedder(_settings(embedding_offline=True))

    with pytest.raises(RuntimeError, match="本地 Embedding 模型加载失败"):
        embedder._load_model()

    assert [call["local_files_only"] for call in FakeSentenceTransformer.calls] == [True]


def test_embed_texts_applies_document_prefix() -> None:
    embedder = Embedder(
        _settings(
            embedding_profile="e5-base",
            embedding_document_prefix="passage: ",
        )
    )

    embedder.embed_texts(["def main(): pass"])

    assert FakeSentenceTransformer.encoded_batches == [["passage: def main(): pass"]]


def test_embed_query_applies_query_prefix() -> None:
    embedder = Embedder(
        _settings(
            embedding_profile="e5-base",
            embedding_query_prefix="query: ",
        )
    )

    embedder.embed_query("CLI 入口在哪里？")

    assert FakeSentenceTransformer.encoded_batches == [["query: CLI 入口在哪里？"]]


def test_get_instance_reloads_when_profile_changes() -> None:
    baseline = Embedder.get_instance(_settings())
    baseline._load_model()

    changed = Embedder.get_instance(_settings(embedding_profile="bge-m3"))
    changed._load_model()

    assert changed is baseline
    assert [call["model_name"] for call in FakeSentenceTransformer.calls] == [
        "cached-model",
        "cached-model",
    ]
    assert changed._model_name == "BAAI/bge-m3"
