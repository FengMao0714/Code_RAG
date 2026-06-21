# Code_RAG Showcase

这个目录存放面向简历、面试和项目讲解的展示材料。

## Files

- `eval_compare_latest.md`: `vector / lexical / hybrid` 检索模式对比报告。
- `eval_compare_latest.json`: 同一份对比报告的机器可读版本。
- `embedding_model_rationale.md`: Embedding profile 选择理由、隔离索引设计和对比命令。
- `agent_report_demo.md`: 只读 Code Agent 对本仓库的分析报告示例。
- `resume_bullets.md`: 可直接改写进简历的项目描述。
- `cli_demo.md`: 常用演示命令和面试讲解顺序。

## Regenerate

当前 `eval_compare_latest.*` 是基于本机已有索引快照生成的格式示例。代码或 `evals/code_rag_golden.yaml` 更新后，应先刷新索引再重新生成：

```powershell
uv run code-rag index .
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 `
  --compare-modes vector,lexical,hybrid `
  --output Docs/showcase/eval_compare_latest.json `
  --markdown Docs/showcase/eval_compare_latest.md

uv run code-rag embeddings list
uv run code-rag index . --embedding-profile baseline
uv run code-rag index . --embedding-profile bge-m3
uv run code-rag index . --embedding-profile e5-base
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 `
  --mode hybrid `
  --compare-embeddings baseline,bge-m3,e5-base `
  --output Docs/showcase/embedding_compare_latest.json `
  --markdown Docs/showcase/embedding_compare_latest.md

uv run code-rag agent . "评估 Code_RAG 的检索架构、关键文件、风险和回归测试重点" `
  --output Docs/showcase/agent_report_demo.md `
  --format markdown
```

如果本地 CPU 全量 embedding 较慢，可以先用已有索引查看报告格式，再在有空闲资源时刷新索引。
