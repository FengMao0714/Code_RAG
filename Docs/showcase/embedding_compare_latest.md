# Embedding Eval Comparison — code_rag_golden

- Generated at: 2026-06-21T20:16:03
- Repository: `.`
- Retrieval mode: `hybrid`
- top_k: 8

## Profile Summary

| Profile | Model | Status | Recall@1 | Recall@3 | Recall@8 | MRR | File Hit | Symbol Hit | Avg Latency |
|---------|-------|--------|----------|----------|----------|-----|----------|------------|-------------|
| baseline | BAAI/bge-large-zh-v1.5 | indexed | 57.89% | 78.95% | 94.74% | 0.6952 | 73.68% | 63.16% | 450.10ms |
| bge-m3 | BAAI/bge-m3 | indexed | 47.37% | 73.68% | 94.74% | 0.6289 | 78.95% | 68.42% | 267.85ms |
| e5-base | intfloat/multilingual-e5-base | indexed | 47.37% | 63.16% | 94.74% | 0.6175 | 84.21% | 63.16% | 229.65ms |

## Model Rationale

- `baseline` / `BAAI/bge-large-zh-v1.5`: Current baseline used by Code_RAG; keeps existing Chinese retrieval behavior and preserves legacy index keys.
- `bge-m3` / `BAAI/bge-m3`: Recommended candidate for cross-lingual codebase retrieval because Code_RAG mixes Chinese questions with English and multilingual code identifiers.
- `e5-base` / `intfloat/multilingual-e5-base`: Smaller multilingual contrast profile with the E5 query/passage prefix convention; useful as a cost and latency baseline.

## Notes

Embedding model claims should be based on this local golden-query report, not on model popularity alone.
