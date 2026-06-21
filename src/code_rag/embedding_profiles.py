"""Embedding profile registry.

Profiles give model choices stable IDs, model names, prompt prefixes, and
rationale text so evaluation reports can explain what was compared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from code_rag.config import Settings


class EmbeddingProfileError(ValueError):
    """Raised when an embedding profile cannot be resolved."""


@dataclass(frozen=True)
class EmbeddingProfile:
    """Resolved embedding profile configuration."""

    profile_id: str
    model_name: str
    query_prefix: str = ""
    document_prefix: str = ""
    rationale: str = ""
    is_builtin: bool = True

    @property
    def is_baseline(self) -> bool:
        """Whether this profile should preserve legacy collection keys."""
        return self.profile_id == "baseline"


_BUILTIN_PROFILES: tuple[EmbeddingProfile, ...] = (
    EmbeddingProfile(
        profile_id="baseline",
        model_name="BAAI/bge-large-zh-v1.5",
        rationale=(
            "Current baseline used by Code_RAG; keeps existing Chinese retrieval "
            "behavior and preserves legacy index keys."
        ),
    ),
    EmbeddingProfile(
        profile_id="bge-m3",
        model_name="BAAI/bge-m3",
        rationale=(
            "Recommended candidate for cross-lingual codebase retrieval because "
            "Code_RAG mixes Chinese questions with English and multilingual code identifiers."
        ),
    ),
    EmbeddingProfile(
        profile_id="e5-base",
        model_name="intfloat/multilingual-e5-base",
        query_prefix="query: ",
        document_prefix="passage: ",
        rationale=(
            "Smaller multilingual contrast profile with the E5 query/passage prefix "
            "convention; useful as a cost and latency baseline."
        ),
    ),
)

_BUILTIN_BY_ID = {profile.profile_id: profile for profile in _BUILTIN_PROFILES}


def list_embedding_profiles() -> list[EmbeddingProfile]:
    """Return built-in profiles in stable display order."""
    return list(_BUILTIN_PROFILES)


def resolve_embedding_profile(settings: Settings) -> EmbeddingProfile:
    """Resolve settings into a concrete embedding profile.

    Built-in profile IDs choose their registered model and rationale. The
    special ``custom`` profile lets callers evaluate an arbitrary model name
    from ``settings.embedding_model`` while still receiving an isolated key.
    """
    profile_id = (settings.embedding_profile or "baseline").strip()
    if not profile_id:
        profile_id = "baseline"

    if profile_id in _BUILTIN_BY_ID:
        profile = _BUILTIN_BY_ID[profile_id]
    elif profile_id == "custom":
        profile = EmbeddingProfile(
            profile_id="custom",
            model_name=settings.embedding_model,
            rationale="Custom embedding model supplied through EMBEDDING_MODEL.",
            is_builtin=False,
        )
    else:
        known = ", ".join(profile.profile_id for profile in _BUILTIN_PROFILES)
        raise EmbeddingProfileError(
            f"未知 embedding profile: {profile_id}. 可用 profile: {known}, custom"
        )

    query_prefix = settings.embedding_query_prefix
    document_prefix = settings.embedding_document_prefix
    if query_prefix is not None or document_prefix is not None:
        profile = EmbeddingProfile(
            profile_id=profile.profile_id,
            model_name=profile.model_name,
            query_prefix=profile.query_prefix if query_prefix is None else query_prefix,
            document_prefix=profile.document_prefix if document_prefix is None else document_prefix,
            rationale=profile.rationale,
            is_builtin=profile.is_builtin,
        )
    return profile


def embedding_profile_key(collection_key: str, profile: EmbeddingProfile) -> str:
    """Return a profile-aware collection/tracker key.

    The baseline profile intentionally preserves old keys for compatibility.
    Every other profile receives a readable suffix so independent indexes can
    coexist for the same repository.
    """
    if profile.is_baseline:
        return collection_key
    safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "-", profile.profile_id).strip("-")
    if not safe_profile:
        safe_profile = "custom"
    return f"{collection_key}__emb_{safe_profile}"
