# Embedding Eval Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reproducible embedding-profile indexing and evaluation comparison to Code_RAG.

**Architecture:** Introduce a small embedding profile registry, thread the selected profile through settings and repository identity, and extend eval reporting to compare model profiles without mixing vector collections.

**Tech Stack:** Python 3.11, Typer, ChromaDB, sentence-transformers, pytest, ruff.

---

### Task 1: Embedding Profile Registry

**Files:**
- Create: `src/code_rag/embedding_profiles.py`
- Modify: `src/code_rag/config.py`
- Test: `tests/test_embedding_profiles.py`

- [ ] Write failing tests for built-in profile lookup, raw model fallback, prefix handling, and invalid profile errors.
- [ ] Implement `EmbeddingProfile`, `resolve_embedding_profile()`, `list_embedding_profiles()`, and `embedding_profile_key()`.
- [ ] Add settings fields for `embedding_profile`, `embedding_query_prefix`, and `embedding_document_prefix`.
- [ ] Verify with `uv run --frozen pytest tests/test_embedding_profiles.py tests/test_embedder.py -q`.

### Task 2: Use Profiles In Embedder

**Files:**
- Modify: `src/code_rag/indexer/embedder.py`
- Test: `tests/test_embedder.py`

- [ ] Write failing tests proving query/document prefixes are applied before `encode()`.
- [ ] Update `Embedder` to resolve its profile and apply document/query prefixes separately.
- [ ] Preserve local-cache-first loading behavior.
- [ ] Verify with `uv run --frozen pytest tests/test_embedder.py -q`.

### Task 3: Isolate Indexes By Embedding Profile

**Files:**
- Modify: `src/code_rag/repository/resolver.py`
- Modify: `src/code_rag/services/index_service.py`
- Modify: `src/code_rag/services/manifest_service.py`
- Test: `tests/test_repository.py`, `tests/test_index_service.py`, `tests/test_cli_remote.py`

- [ ] Write failing tests showing `bge-m3` receives a different collection/tracker key from the baseline profile.
- [ ] Add an optional embedding identity suffix helper that keeps baseline keys backward compatible.
- [ ] Ensure index, status, search, remove, and manifest paths use the profile-aware key.
- [ ] Verify with targeted repository, index, and CLI tests.

### Task 4: CLI Profile Selection And Listing

**Files:**
- Modify: `src/code_rag/cli.py`
- Test: `tests/test_cli.py`

- [ ] Write failing tests for `code-rag embeddings list`, `index --embedding-profile`, `search --embedding-profile`, `status --embedding-profile`, `remove --embedding-profile`, and `eval --embedding-profile`.
- [ ] Add an `embeddings` Typer subcommand and shared option plumbing.
- [ ] Print profile/model information in status and index output.
- [ ] Verify with `uv run --frozen pytest tests/test_cli.py -q`.

### Task 5: Compare Embedding Profiles

**Files:**
- Modify: `src/code_rag/services/eval_service.py`
- Modify: `src/code_rag/evaluation/report.py`
- Modify: `src/code_rag/cli.py`
- Test: `tests/test_evaluation.py`, `tests/test_cli.py`

- [ ] Write failing tests for `--compare-embeddings baseline,bge-m3,e5-base`.
- [ ] Add comparison result objects that include metrics or missing-index status per profile.
- [ ] Add JSON and Markdown rendering for embedding comparison with rationale text.
- [ ] Verify with targeted eval and CLI tests.

### Task 6: Documentation And Showcase

**Files:**
- Modify: `README.md`
- Modify: `Docs/showcase/README.md`
- Modify: `Docs/showcase/resume_bullets.md`
- Create or modify: `Docs/showcase/embedding_model_rationale.md`

- [ ] Document why `bge-m3` is the recommended candidate and why results must be evaluated locally.
- [ ] Add copy-paste commands for indexing and comparing all profiles.
- [ ] Rewrite resume bullets around reproducible evaluation rather than unsupported model superiority claims.
- [ ] Verify docs commands with `uv run code-rag --help`.

### Task 7: Full Verification

**Files:**
- No new files.

- [ ] Run `uv run --frozen pytest -q`.
- [ ] Run `uv run --frozen ruff check src/ tests/`.
- [ ] Run `uv run --frozen ruff format --check src/ tests/`.
- [ ] Run `uv run code-rag --help`.
- [ ] Run a lightweight eval command against the current index or document any missing-model download limitation.

