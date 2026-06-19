# Retrieval Eval Comparison — code_rag_golden

- Generated at: 2026-06-19T23:56:48
- Repository: `.`
- top_k: 8

> Note: this report is a format and workflow artifact generated from the current
> local ChromaDB index snapshot. If source files or `evals/code_rag_golden.yaml`
> changed, run `uv run code-rag index .` before regenerating the report.

## Mode Summary

| Mode | Recall@1 | Recall@3 | Recall@8 | MRR | File Hit | Symbol Hit | Avg Latency |
|------|----------|----------|----------|-----|----------|------------|-------------|
| vector | 31.58% | 36.84% | 42.11% | 0.3421 | 26.32% | 36.84% | 308.55ms |
| lexical | 42.11% | 47.37% | 47.37% | 0.4386 | 26.32% | 42.11% | 26.60ms |
| hybrid | 36.84% | 47.37% | 47.37% | 0.4123 | 26.32% | 42.11% | 108.55ms |

## Notes

Hybrid retrieval should be evaluated against vector and lexical baselines before claiming ranking improvements.
