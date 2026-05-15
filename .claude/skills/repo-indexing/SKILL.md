---
name: repo-indexing
description: Use when implementing or reviewing repository ingestion, file filtering, parsing, code chunking, metadata extraction, embeddings, or incremental indexing for Code_RAG.
---

# Repository Indexing

Indexing must be deterministic, explainable, and safe.

File filtering rules:

- Exclude secrets and credentials: `.env*`, private keys, certificates, tokens, cloud credentials, database dumps.
- Exclude generated or noisy content: `node_modules`, `.git`, `.venv`, `dist`, `build`, `.next`, `coverage`, caches, lockfile-heavy vendor trees, binary files, images, videos, archives.
- Apply file size limits and language allowlists before reading full content.
- Record exclusion reasons for observability.

Parsing and chunking rules:

- Prefer parser or AST boundaries for functions, classes, methods, interfaces, types, modules, routes, and config blocks.
- Preserve line numbers. Store both start and end lines for every chunk.
- Use deterministic chunk IDs from repo id, snapshot id, path, symbol path or line range, and chunk hash.
- Keep raw source text as the primary embedded field. Optional summaries must be separate fields.
- Include parent context sparingly: file path, symbol path, signature, docstring, and nearby imports are usually more useful than large parent chunks.
- Avoid overlapping chunks unless a test shows it improves retrieval.

Incremental indexing rules:

- Use content hashes to detect unchanged files.
- Mark deleted chunks when files disappear.
- Treat renames as changed path metadata unless a rename detector is explicitly implemented.
- Store embedding model name and dimensions so migrations are possible.
- Batch embedding calls and handle partial failures idempotently.

Testing expectations:

- Add fixtures for tiny repositories with known files, symbols, and expected chunks.
- Test excludes, stable IDs, line ranges, deletion handling, and re-index idempotency.
- For parser changes, include at least one malformed or partial source file.
