---
name: rag-security-privacy
description: Use when handling repository content, file system access, secrets, credentials, embeddings sent to external providers, path inputs, multi-tenant data, or deployment/security choices for Code_RAG.
---

# RAG Security And Privacy

Treat source repositories as sensitive data. Code can contain credentials, unreleased business logic, private endpoints, proprietary algorithms, and personal data.

Required safeguards:

- Default to allowlisted repository roots. Reject paths outside the configured root after resolving symlinks and relative segments.
- Never index files matching secret patterns, credential names, private key headers, local env files, or cloud config files.
- Keep `.env` out of commits. Only commit `.env.example` with placeholders.
- Log metadata and counts, not full source text or API keys.
- Redact secrets before displaying snippets in diagnostics.
- Store source snippets and embeddings in the intended local or approved database only.
- Document any external provider that receives code text, including embedding and reranking APIs.

Threats to consider:

- Prompt injection inside indexed code comments or markdown files.
- Path traversal during repository selection.
- Cross-repository data leakage in multi-repo or multi-user search.
- Stale chunks from deleted files.
- Secret leakage through embeddings, logs, traces, cached prompts, or evaluation fixtures.
- Overbroad retrieval filters returning private results to the wrong user.

When in doubt, stop and ask the user before sending source content to a new third-party service or changing data retention behavior.
