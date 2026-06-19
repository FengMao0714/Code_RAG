# CLI Demo Script

## 1. Show Help

```powershell
uv run code-rag --help
```

讲解点：这是一个工程化 CLI，不是 notebook demo；入口命令覆盖索引、检索、问答、评测、Agent 和缓存管理。

## 2. Search With Hybrid Retrieval

```powershell
uv run code-rag search . "HybridRetriever 如何融合向量和词法召回？" --mode hybrid --explain
```

讲解点：`--explain` 可以展示命中来自 vector、lexical 还是 hybrid 融合，方便定位召回质量问题。

## 3. Compare Retrieval Modes

```powershell
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 `
  --compare-modes vector,lexical,hybrid `
  --output Docs/showcase/eval_compare_latest.json `
  --markdown Docs/showcase/eval_compare_latest.md
```

讲解点：不要只宣称 hybrid 更好，要用同一套 golden query 同时跑三个模式。

## 4. Generate Read-Only Agent Report

```powershell
uv run code-rag agent . "评估 Code_RAG 的检索架构、关键文件、风险和回归测试重点" `
  --output Docs/showcase/agent_report_demo.md `
  --format markdown
```

讲解点：Agent 不直接改代码，而是输出可复核的证据、关键文件、风险和建议测试。

## 5. Validate Engineering Quality

```powershell
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
```

讲解点：这个项目的展示价值来自可复现验证，不是只堆 RAG 名词。
