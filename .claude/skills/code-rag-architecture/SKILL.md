---
name: code-rag-architecture
description: Use when designing or changing the architecture of the Code_RAG project, including ingestion, parsing, embeddings, vector storage, retrieval, reranking, answer synthesis, API boundaries, or evaluation strategy.
---

# Code RAG Architecture

Before implementing architecture changes, identify the exact vertical slice being changed: ingestion, parsing, chunking, embedding, vector storage, retrieval, reranking, answer synthesis, UI, API, or evaluation.

Keep the system modular around stable data contracts:

- `RepositorySnapshot`: repo identity, branch, commit or snapshot id, root path, scan timestamp.
- `SourceFile`: normalized path, language, size, content hash, include or exclude reason.
- `CodeSymbol`: symbol path, kind, parent, file path, start line, end line, signature, docstring.
- `CodeChunk`: stable id, text, file path, symbol path, line range, source kind, chunk hash, metadata.
- `EmbeddingRecord`: chunk id, embedding model, dimensions, vector, embedded timestamp.
- `RetrievalHit`: chunk id, score components, source metadata, snippet, reason for match.
- `AnswerCitation`: path, line range, symbol, chunk id, quote or concise evidence.

Recommended baseline architecture:

1. Ingest local repositories through an allowlisted root.
2. Filter files before reading content.
3. Parse symbols and line ranges where a parser is available.
4. Chunk code around semantic boundaries first, with token limits as a fallback.
5. Store metadata separately from vectors so re-embedding does not destroy provenance.
6. Use hybrid retrieval: vector similarity plus lexical, path, and symbol-aware signals.
7. Add reranking behind an interface.
8. Generate answers only from retrieved evidence and cite sources.
9. Evaluate retrieval quality with golden queries before expanding scope.

Prefer boring interfaces and fixtures over premature framework lock-in. When choosing a library or service, check current docs first and record why it was chosen.
