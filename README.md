# Code_RAG — 代码仓库 RAG 检索与只读 Code Agent CLI

Code_RAG 是一个面向 **AI/RAG 工程方向** 的代码知识库 CLI：把本地目录或 Git 仓库索引成可检索的代码知识库，支持 AST 语义切片、向量召回、符号词法召回、RRF 混合排序、离线评测和只读 Code Agent 分析。

这个项目的重点不是做一个聊天壳，而是展示一个可复现、可测试、可解释的代码检索系统。

## Highlights

| 能力 | 实现 |
|---|---|
| 多语言 AST 切片 | 基于 tree-sitter 支持 Python / JS / TS / Java / Go / C / C++ / Rust / Ruby / PHP / C# / Swift / Lua / Shell |
| Hybrid Retrieval | 向量检索 + 文件名/符号词法召回 + Reciprocal Rank Fusion |
| 增量索引 | SHA-256 文件追踪，修改/删除文件会清理旧 chunk |
| 统一仓库源 | 同一套 CLI 支持本地路径和 Git URL，远程仓库进入本地 cache |
| 安全边界 | 默认拒绝不安全 URL scheme，远程 URL token 脱敏，未索引远程 search 不触发 clone/fetch |
| 检索评测 | golden query 数据集计算 Recall@k、MRR、file hit、symbol hit、latency |
| 只读 Code Agent | Planner -> Hybrid 检索 -> 证据汇总 -> 风险/测试建议，不自动改文件 |
| 工程质量 | 263 个自动化测试，ruff lint/format，Windows/Linux 友好 |

## Architecture

```text
local path / git URL
        |
        v
   resolve_repo
        |
        v
 RepoScanner -> CodeParser -> CodeChunker -> Embedder -> ChromaDB
        |                                      |
        |                                      v
        |                              IndexTracker + Manifest
        |
user query
        |
        v
Retriever(vector) + LexicalRetriever(symbol/file/source)
        |
        v
RRFReranker -> ContextBuilder -> LLM streaming / search debug / eval / agent
```

核心代码路径：

- `src/code_rag/indexer/`: 扫描、AST 解析、语义切片、Embedding。
- `src/code_rag/retriever/`: 向量检索、词法检索、RRF、mode 校验。
- `src/code_rag/services/`: CLI 背后的业务编排层。
- `src/code_rag/evaluation/`: golden query、指标计算、JSON/Markdown 报告。
- `src/code_rag/agent/`: 只读 Code Agent 的计划、证据汇总和报告导出。

## Quick Start

```powershell
uv sync
Copy-Item .env.example .env

# 索引本地仓库
uv run code-rag index .

# 检索调试，默认 hybrid
uv run code-rag search . "HybridRetriever 如何融合向量和词法召回？" --explain

# 问答，需要 .env 中配置 OpenAI-compatible LLM
uv run code-rag ask . "这个项目的索引流程是什么？"

# 检索评测
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 --mode hybrid

# 多模式对比报告
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 `
  --compare-modes vector,lexical,hybrid `
  --output Docs/showcase/eval_compare_latest.json `
  --markdown Docs/showcase/eval_compare_latest.md

# 只读 Code Agent 报告
uv run code-rag agent . "评估检索架构、关键文件、风险和回归测试重点" `
  --output Docs/showcase/agent_report_demo.md `
  --format markdown
```

## CLI Surface

```text
code-rag embeddings list
code-rag index  <source> [--ref <git-ref>] [--refresh] [--embedding-profile baseline|bge-m3|e5-base|custom]
code-rag search <source> <query> [--mode vector|lexical|hybrid] [--embedding-profile <profile>] [--explain]
code-rag ask    <source> <question> [--mode vector|lexical|hybrid] [--embedding-profile <profile>]
code-rag chat   <source> [--mode vector|lexical|hybrid] [--embedding-profile <profile>]
code-rag eval   <source> --dataset evals/code_rag_golden.yaml [--compare-modes vector,lexical,hybrid]
code-rag eval   <source> --dataset evals/code_rag_golden.yaml --compare-embeddings baseline,bge-m3,e5-base [--auto-index]
code-rag agent  <source> <task> [--output report.md] [--format markdown|json]
code-rag list
code-rag status <source>
code-rag remove <source> --yes [--with-cache]
code-rag cache list
code-rag cache prune --yes
```

`<source>` 可以是本地路径，也可以是安全的 Git URL。默认允许 `https://`、`ssh://`、`git+ssh://`；`file://` 只在配置 `ALLOW_FILE_REMOTE=true` 时启用，主要用于离线测试。

## Retrieval Design

Code_RAG 同时保留三种检索模式：

- `vector`: 使用本地 BGE embedding 查询 ChromaDB，适合语义类问题。
- `lexical`: 扫描 `file_path`、`name`、`parent`、`source`，适合文件名、函数名、类名、CLI 名称等精确定位。
- `hybrid`: 并行执行 vector 和 lexical，再用 RRF 融合排序，是展示和问答默认路径。

`SearchMode` 统一校验 `vector|lexical|hybrid`，避免 CLI、service、eval 各自维护字符串分支。

## Evaluation

`evals/code_rag_golden.yaml` 目前包含 20 条 golden query，覆盖：

- CLI 入口、配置入口、核心类/函数定位。
- scanner、chunker、retriever、vector store、LLM streaming 等流程解释。
- 负样本、歧义样本、安全边界、远程仓库副作用、Agent 报告导出。

报告示例位于 `Docs/showcase/`。如果代码或 golden dataset 更新，先刷新索引再重新生成报告：

Embedding 模型也可以作为实验变量对比。`baseline` 继续使用 `BAAI/bge-large-zh-v1.5` 并兼容旧索引；`bge-m3` 是面向中文问题 + 英文/多语言代码标识符的推荐候选；`e5-base` 是较轻量的多语言对照模型。非 baseline profile 会使用独立 collection / tracker / manifest，避免不同模型的向量空间互相污染。

```powershell
uv run code-rag index .
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 `
  --compare-modes vector,lexical,hybrid `
  --output Docs/showcase/eval_compare_latest.json `
  --markdown Docs/showcase/eval_compare_latest.md
```

## Engineering Notes

- CLI 只负责参数解析和 Rich 展示，索引/查询/评测逻辑下沉到 service 层。
- 只读操作尽量不创建 collection，不 clone/fetch 未索引远程仓库。
- scanner 默认跳过常见依赖目录、二进制文件、敏感文件名、符号链接和文档中的简单 secret 模式。
- 测试使用 fake embedder / fake LLM，真实 ChromaDB 走临时目录，因此 CI 不依赖外部模型下载或真实 LLM。
- Code Agent 明确是 read-only reviewer，不自动应用修改，适合做开发前的证据定位和风险提示。

## Verification

```powershell
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
uv run code-rag --help
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 --mode hybrid
```

Recent local validation after this refactor:

- `263 passed`
- `ruff check`: all checks passed
- `ruff format --check`: 60 files already formatted

## Resume Bullets

更适合直接放简历的版本见 `Docs/showcase/resume_bullets.md`。核心表达可以概括为：

> 构建代码仓库 RAG CLI，基于 tree-sitter 实现 14 种语言 AST 语义切片，结合 BGE Embedding、ChromaDB、符号词法召回与 RRF 混合排序，设计 golden query 评测体系统计 Recall@k/MRR，并以 263 个自动化测试覆盖索引、检索、Git 仓库、评测和只读 Code Agent 流程。

## License

MIT
