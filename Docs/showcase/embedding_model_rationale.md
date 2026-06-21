# Embedding Model Rationale

Code_RAG now treats the embedding model as part of the retrieval experiment, not as a hidden configuration detail. Each non-baseline embedding profile receives an isolated ChromaDB collection, tracker directory, and manifest entry, so evaluation never mixes document vectors from one model with query vectors from another.

## Profiles

| Profile | Model | Why It Exists |
|---|---|---|
| `baseline` | `BAAI/bge-large-zh-v1.5` | Keeps the original Chinese-oriented baseline and preserves legacy collection keys. |
| `bge-m3` | `BAAI/bge-m3` | Recommended candidate for this project because Code_RAG mixes Chinese natural-language questions with English and multilingual code identifiers. |
| `e5-base` | `intfloat/multilingual-e5-base` | Smaller multilingual contrast profile. It uses the E5 `query: ` / `passage: ` prefix convention and gives a cost/latency comparison point. |

## How To Compare

Index each profile first:

```powershell
uv run code-rag index . --embedding-profile baseline
uv run code-rag index . --embedding-profile bge-m3
uv run code-rag index . --embedding-profile e5-base
```

Then compare retrieval quality:

```powershell
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 `
  --mode hybrid `
  --compare-embeddings baseline,bge-m3,e5-base `
  --output Docs/showcase/embedding_compare_latest.json `
  --markdown Docs/showcase/embedding_compare_latest.md
```

If you want the command to build missing profile-specific indexes automatically, add `--auto-index`. This can download large Hugging Face models, so the default is intentionally explicit.

## Interview Framing

Do not claim that `bge-m3` is better just because it is newer. The stronger project story is:

1. The system noticed a correctness risk: model switching could pollute evaluation by reusing old collections.
2. The fix made embedding profiles part of index identity.
3. The project compares models with the same golden-query dataset and reports Recall@k, MRR, hit rates, and latency.
4. `bge-m3` is the recommended candidate before evaluation because the workload is cross-lingual; the final model choice is based on local results.

