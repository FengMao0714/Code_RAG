# Embedding Eval Redesign

## Goal

Upgrade Code_RAG from a single-embedding demo into a reproducible retrieval-evaluation project that can index the same repository with different embedding profiles, compare retrieval quality, and explain why a model was selected.

## Problem

The current system stores one collection per repository identity. `embedding_model` is recorded in the manifest, but it is not part of the collection key. Switching models can therefore mix old document vectors with new query vectors, making evaluation results unreliable.

## Recommended Scope

Use `BAAI/bge-large-zh-v1.5` as the baseline, add `BAAI/bge-m3` as the recommended cross-lingual profile, and add `intfloat/multilingual-e5-base` as a smaller contrast profile. The default profile should remain backward compatible for existing users, but new comparison commands must isolate indexes by embedding profile.

## Design

Add an embedding profile registry with stable profile IDs, model names, optional query/document prefixes, and rationale text. CLI options should accept either a profile ID or a raw model name. Built-in profiles make the project easier to explain in interviews, while raw model support keeps it flexible.

Index identity must include the embedding profile when explicitly selected. The baseline profile keeps the old collection key for compatibility; non-baseline profiles derive a suffix from the profile ID/model. Tracker and manifest paths follow the same key, so each model has independent incremental state.

Evaluation gains a `--compare-embeddings` mode. For each profile, the command checks whether the profile-specific index exists. If it does not exist and `--auto-index` is not set, the report marks the profile as missing instead of silently falling back. With `--auto-index`, it indexes that profile before running the existing `vector / lexical / hybrid` comparison path.

Reports should include embedding profile, model name, indexing status, rationale, and the standard metrics. Documentation should state that model choice is empirical: `bge-m3` is chosen as a strong candidate for Chinese natural-language questions over English/multilingual code identifiers, but final claims must come from local golden-query results.

## Non-Goals

Do not add reranker training, external benchmark downloads, sparse-vector storage, or a web UI in this change. Do not claim one model is better unless the local evaluation report supports it.

