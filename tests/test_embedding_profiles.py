"""Embedding profile registry tests."""

from __future__ import annotations

import pytest

from code_rag.config import Settings
from code_rag.embedding_profiles import (
    EmbeddingProfileError,
    embedding_profile_key,
    list_embedding_profiles,
    resolve_embedding_profile,
)


def _settings(**kwargs) -> Settings:
    return Settings(
        llm_api_key="x",
        llm_base_url="http://x",
        llm_model="m",
        chroma_persist_dir="tmp/chroma",
        index_tracker_dir="tmp/indexes",
        repo_cache_dir="tmp/repos",
        **kwargs,
    )


def test_lists_builtin_profiles() -> None:
    profiles = list_embedding_profiles()

    assert [profile.profile_id for profile in profiles] == [
        "baseline",
        "bge-m3",
        "e5-base",
    ]
    assert profiles[1].model_name == "BAAI/bge-m3"
    assert "cross-lingual" in profiles[1].rationale.lower()


def test_resolves_baseline_from_settings_default() -> None:
    profile = resolve_embedding_profile(_settings())

    assert profile.profile_id == "baseline"
    assert profile.model_name == "BAAI/bge-large-zh-v1.5"
    assert profile.is_builtin is True


def test_resolves_builtin_profile_and_overrides_model() -> None:
    profile = resolve_embedding_profile(_settings(embedding_profile="bge-m3"))

    assert profile.profile_id == "bge-m3"
    assert profile.model_name == "BAAI/bge-m3"


def test_resolves_raw_model_as_custom_profile() -> None:
    profile = resolve_embedding_profile(
        _settings(embedding_profile="custom", embedding_model="nomic-ai/nomic-embed-text-v1.5")
    )

    assert profile.profile_id == "custom"
    assert profile.model_name == "nomic-ai/nomic-embed-text-v1.5"
    assert profile.is_builtin is False


def test_applies_prefix_overrides() -> None:
    profile = resolve_embedding_profile(
        _settings(
            embedding_profile="e5-base",
            embedding_query_prefix="question: ",
            embedding_document_prefix="code: ",
        )
    )

    assert profile.query_prefix == "question: "
    assert profile.document_prefix == "code: "


def test_unknown_builtin_like_profile_raises() -> None:
    with pytest.raises(EmbeddingProfileError, match="未知 embedding profile"):
        resolve_embedding_profile(_settings(embedding_profile="does-not-exist"))


def test_baseline_profile_key_preserves_old_collection_key() -> None:
    assert embedding_profile_key("repo-key", resolve_embedding_profile(_settings())) == "repo-key"


def test_non_baseline_profile_key_is_isolated() -> None:
    profile = resolve_embedding_profile(_settings(embedding_profile="bge-m3"))

    assert embedding_profile_key("repo-key", profile) == "repo-key__emb_bge-m3"
