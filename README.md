# Code_RAG — 代码知识库 RAG 问答 CLI

> 基于 RAG（检索增强生成）的代码仓库问答 CLI。
> tree-sitter AST 语义切片 · ChromaDB 向量存储 · BGE Embedding · 符号词法召回 · RRF 混合排序 · 可复现检索评测 · 本地路径 & Git 仓库 · 轻量 Code Agent

适合 AI / RAG 方向实习简历展示。201 个自动化测试 + Windows/Linux CI 全绿。

---

## 核心亮点

| 特性 | 说明 |
|------|------|
| **14 种语言 AST 解析** | tree-sitter 解析 Python / JS / TS / Java / Go / C / C++ / Rust / Ruby / PHP / C# / Swift / Lua / Shell |
| **AST 语义切片** | 按函数 / 类 / 模块 / 文档四种语义单元切片；超长函数基于行边界二次切分 |
| **本地 + Git 仓库** | 同一套命令支持本地路径 / Git URL，远程仓库自动 clone 到 `repo_cache_dir` 缓存 |
| **Hybrid Retrieval** | 向量检索 + 符号/文件名词法召回 + RRF 融合排序（k=60，支持通道权重） |
| **Metadata Boost** | 从查询提取标识符（camelCase / snake_case / 文件名 / 中文 2 字组），匹配 chunk 的 file_path / name / parent 重排 |
| **增量索引** | SHA-256 哈希追踪 + Manifest 记录原始路径 / 模型 / 文件数 / chunk 数 / 最后索引时间 |
| **检索评测** | 12 条 golden query，Recall@1/3/8、MRR、文件/符号命中率、JSON + Markdown 报告 |
| **轻量 Code Agent** | Planner → Hybrid 检索 → 证据汇总 → 离线 Reasoner/Reviewer，只读分析，输出关键文件 / 修改建议 / 风险 / 测试 |
| **离线可测试** | 全部测试不调用真实 LLM、不下载 Embedding 模型，依赖 fake 组件 + 真实 ChromaDB（tmp_path） |
| **低置信度提示** | 无证据或全 doc 命中时显示原因，不诱导 LLM 硬答 |
| **流式问答** | OpenAI 兼容接口 + streaming chunk 输出 |
| **Windows/Linux CI** | GitHub Actions 跑 ruff check / format / pytest |

---

## 架构概览

```
代码仓库
   │
   ▼
┌────────┐  ┌────────┐  ┌─────────┐  ┌──────────┐
│scanner │─▶│ parser │─▶│ chunker │─▶│ embedder │
│扫描+过滤│  │tree-sitter│ │语义切片 │  │ BGE 本地 │
└────────┘  └────────┘  └─────────┘  └────┬─────┘
                                          │
                                   ┌──────▼──────┐
                                   │ vector_store│
                                   │  ChromaDB   │
                                   └──────┬──────┘
                                          │
   用户提问 ─┐                              │
            ▼                              │
   ┌──────────────┐  vector  ┌────────┐   │
   │HybridRetriever│◀────────│Embedder│◀──┘
   │ vector +     │          └────────┘
   │ lexical +    │
   │ RRFReranker  │  lexical  ┌────────┐
   │              │◀─────────│文件/符号│
   └──────┬───────┘          │关键词 │
          │                   └────────┘
          ▼
   ┌──────────┐
   │   LLM    │  ← 流式输出
   │ OpenAI 兼容│
   └──────────┘
```

**索引**：`scanner → parser → chunker → embedder → ChromaDB + Manifest`
**检索**：`query → Embedder + Lexical → RRF 融合 → ContextBuilder → LLM`
**评测**：`golden query → Hybrid/Vector/Lexical → Recall@k / MRR / 报告`

---

## 项目结构

```
src/code_rag/
├── cli.py                       # CLI 入口 (typer + rich)
├── config.py                    # pydantic-settings
├── indexer/
│   ├── scanner.py               # 仓库扫描 + .gitignore 过滤
│   ├── parser.py                # tree-sitter 多语言 AST
│   ├── chunker.py               # 语义切片 + 超长二次切分
│   └── embedder.py              # BGE 本地 Embedding (单例)
├── store/
│   ├── vector_store.py          # ChromaDB 封装 (upsert/query/delete/stats)
│   ├── index_tracker.py         # SHA-256 增量追踪
│   └── manifest.py              # （已迁移到 services/manifest_service.py）
├── repository/                  # 【新增】仓库源抽象
│   ├── models.py                # RepoSource / RepoIdentity / ResolvedRepo
│   ├── parser.py                # 本地路径 / Git URL 解析
│   ├── local.py                 # 本地路径 provider
│   ├── git.py                   # Git clone / fetch / checkout
│   ├── cache.py                 # 远程仓库缓存目录 + 稳定 collection_key
│   └── resolver.py              # resolve_repo 统一入口
├── retriever/
│   ├── retriever.py             # 向量检索 + metadata boost + 上下文组装
│   ├── lexical.py               # 词法检索（符号/文件/源码子串）
│   ├── rerank.py                # RRF 重排序（k=60，通道权重）
│   └── hybrid.py                # 向量 + 词法 + RRF 融合
├── generator/
│   ├── llm.py                   # OpenAI 兼容 LLM + streaming
│   └── prompts.py               # System / User / Context 模板
├── services/                    # 业务编排层
│   ├── index_service.py         # 索引流程（CLI 不再做业务）
│   ├── query_service.py         # 检索 + 低置信度评估
│   ├── manifest_service.py      # Manifest 读写 + 状态查询
│   └── eval_service.py          # 评测编排
├── evaluation/                  # 检索评测
│   ├── dataset.py               # Golden query YAML 加载
│   ├── metrics.py               # Recall@k / MRR / hit rate
│   └── report.py                # JSON + Markdown 报告
└── agent/                       # 【新增】轻量 Code Agent
    ├── models.py                # AgentTask / AgentStep / AgentPlan / AgentReport
    ├── planner.py               # Planner：把任务拆成 3~6 个子问题
    └── code_agent.py            # CodeAgent：plan → 检索 → 证据汇总 → 离线 Reasoner/Reviewer

evals/
└── code_rag_golden.yaml         # 12 条 golden query

tests/                           # 201 个测试
├── conftest.py                  # FakeEmbedder / FakeLLMClient
├── test_scanner.py              # 21
├── test_parser_chunker.py       # 18
├── test_index_tracker.py        # 9
├── test_vector_store.py         # 12
├── test_retriever.py            # 28
├── test_cli.py                  # 8
├── test_index_service.py
├── test_query_service.py
├── test_manifest_service.py
├── test_lexical.py              # 词法 + RRF + Hybrid
├── test_evaluation.py           # golden / 指标 / 报告
├── test_repository.py           # 【新增】解析 / 缓存 / 本地 / Git / resolver
├── test_cli_remote.py           # 【新增】cache 子命令 + 远程 e2e
└── test_agent.py                # 【新增】Planner / CodeAgent / agent CLI
```

---

## 快速开始

```bash
# 安装
uv sync
cp .env.example .env
# 编辑 .env 填入 LLM API Key

# 索引（本地路径）
uv run code-rag index /path/to/your/repo

# 索引（Git URL，自动 clone 到 repo_cache_dir）
uv run code-rag index https://github.com/owner/repo --ref main

# 提问
uv run code-rag ask /path/to/your/repo "这个项目的核心架构是什么？"

# 交互
uv run code-rag chat /path/to/your/repo

# 轻量 Code Agent：只读分析 + 修改计划
uv run code-rag agent /path/to/your/repo "把登录流程拆成子任务并指出关键文件" --plan-only
```

---

## CLI 命令

```bash
# 索引 / 检索 / 状态（同时支持本地路径与 Git URL）
uv run code-rag index <source> [--ref <git-ref>] [--refresh]   # 索引（增量），支持 Git
uv run code-rag ask   <source> "<问题>"  [--ref <git-ref>]      # 单次问答
uv run code-rag chat  <source> [--ref <git-ref>]               # 交互模式
uv run code-rag search <source> "<问题>" \
    --mode hybrid --top-k 8 --explain                         # 检索调试（vector/lexical/hybrid）
uv run code-rag list                                           # 已索引仓库（含原始路径、类型、ref）
uv run code-rag status <source> [--ref <git-ref>]              # 索引状态（git 仓库展示 URL / ref / commit / cache）
uv run code-rag remove <source> --yes [--with-cache]           # 删除索引（可选同步删远程缓存）
uv run code-rag eval <source> \
    --dataset evals/code_rag_golden.yaml \
    --top-k 8 \
    --output reports/eval_latest.json \
    --markdown reports/eval_latest.md                          # 检索评测（不调用 LLM）
uv run code-rag agent <source> "<任务>" [--plan-only]          # 轻量 Code Agent（只读）

# 远程仓库缓存管理
uv run code-rag cache list                                     # 列出所有远程仓库缓存
uv run code-rag cache prune --yes                              # 清理所有远程仓库缓存
```

---

## 检索流程详解

1. **Embedder** 把问题转成 BGE 向量。
2. **Vector 通道**：`Retriever` 从 ChromaDB 召回 `top_k * 5`（≥50）候选，不做距离过滤。
3. **Metadata Boost**：从问题中提取 `file_path` / `name` / `parent` 匹配的标识符，相关 chunk 排前。
4. **距离阈值过滤**（默认 0.7）：过滤低相关结果；无结果时回退到不限距离 top_k（保底）。
5. **Lexical 通道**（hybrid）：按 `file_path`/`name`/`source` 词法匹配加权。
6. **RRF 融合**：`score = Σ weight_c / (k + rank_c)`，k=60；可选通道权重。
7. **ContextBuilder**：格式化 chunk 为 LLM prompt，含 score / file / lines / language。
8. **LLM streaming**：流式输出。

`search --explain` 会在每条结果上标 `stage=vector|lexical|hybrid`，方便调优。

---

## 检索评测

```bash
# 离线评测（不调用 LLM）
uv run code-rag eval . \
    --dataset evals/code_rag_golden.yaml \
    --top-k 8 \
    --mode hybrid \
    --output reports/eval_latest.json \
    --markdown reports/eval_latest.md
```

`evals/code_rag_golden.yaml` 包含 12 条覆盖以下类别的问题：

- 符号定位（CLI 入口、scanner 等）
- 配置定位（pyproject.toml [project.scripts]）
- 流程解释（增量索引、超长切片、metadata boost、upsert、streaming）
- 混合检索 / RRF
- 评测命令本身
- 负样本（Web UI 是否实现）
- 歧义样本（list / status / remove）

每条 query 包含 `expected_files` 和 `expected_symbols`，
evaluator 计算 `Recall@1 / Recall@3 / Recall@8 / MRR / file_hit_rate / symbol_hit_rate / avg_latency_ms`，
并输出失败样例（未命中文件 / 未命中符号）。

---

## 测试与 CI

```bash
uv run --frozen pytest -q                 # 201 个测试
uv run --frozen ruff check src/ tests/    # lint
uv run --frozen ruff format --check src/ tests/
```

测试设计原则：

- **零网络依赖**（fake LLM / SHA-256 Embedding）
- **真实 ChromaDB**（tmp_path 隔离）
- **离线可跑**（不下载 1.3GB BGE 模型）

`.github/workflows/ci.yml` 在 `ubuntu-latest` 和 `windows-latest` 上跑全套验证。

---

## 远程 Git 仓库 & Code Agent

### 本地路径 / Git URL 同一套命令

`index / ask / chat / search / status / remove / eval / agent` 等命令
都接受 **本地路径** 与 **Git URL** 两种输入。Git URL 首次使用时会被自动
clone 到 `~/.code-rag/repos/<name>_<8hex>/worktree`，并以 `cache list` /
`cache prune` 子命令管理缓存。

```bash
# 远程仓库索引（自动 clone 到 repo_cache_dir）
uv run code-rag index https://github.com/psf/requests --ref main
uv run code-rag index git@github.com:owner/repo.git --ref v0.1.0

# 切换 ref / 强制刷新
uv run code-rag index https://github.com/owner/repo --ref dev --refresh

# 检索 / 提问 / 评测 / agent 一致接口
uv run code-rag ask https://github.com/owner/repo "如何发送 POST 请求？" --ref main
uv run code-rag eval https://github.com/owner/repo \
    --dataset evals/code_rag_golden.yaml --mode hybrid

# 删除索引时可选同步删远程缓存
uv run code-rag remove https://github.com/owner/repo --yes --with-cache
```

### 轻量 Code Agent（只读）

`agent` 命令把"自然语言任务"拆成 3~6 个子问题，
对每个子问题执行 Hybrid 检索并按文件聚合证据，
最后由离线的 Reasoner / Reviewer 输出结构化报告。
整个流程**只读**、**不调用 LLM**、**不会修改文件**，
可作为安全的人工复核前置步骤。

```bash
uv run code-rag agent . "把登录流程拆成子任务并指出关键文件" --plan-only
# 输出：
#   >> 任务理解
#   >> 计划拆解（1. 2. 3. ...）
#   >> 关键文件（main.py, utils.py, ...）
#   >> 修改建议（只读，不会自动应用）
#   >> 风险点
#   >> 建议运行的测试
#   >> 引用证据
#   >> Reviewer 备注
```

设计要点：

- **Planner**：按标点切分任务句，<3 条时用架构 / 关键文件 / 风险 / 测试 等
  角度兜底补全，>6 条时截断；每条子问题附 rationale。
- **Retriever**：复用 `HybridRetriever`（vector + lexical + RRF），
  每个子问题召回 top_k=5。
- **Evidence Builder**：按 `file_path` 聚合证据，统计命中次数生成关键文件列表。
- **Reasoner**：离线模板生成 understanding / suggested_changes / risks /
  suggested_tests（命中"性能"等关键词时追加 benchmark 建议）。
- **Reviewer**：所有 evidence 为空时标 `insufficient_evidence=True`，
  拒绝臆测并附"证据不足，建议补充信息"备注。

---

### Q1: 为什么按 AST 语义切片而不是固定长度？

固定长度切片会把函数 / 类从中间截断，LLM 看不到完整语义。
本项目按 tree-sitter 解析出的语义单元切片：一个函数 / 类 / 模块 = 一个 chunk。
超长函数再做基于空行 / 语句结束符的二次切分。

### Q2: 为什么需要 Hybrid Retrieval？

向量检索在中英文跨语言场景下天然距离偏高，"CLI 入口在哪里"和英文代码之间的
cosine 距离通常 > 0.5。词法检索可以直接命中 `cli.py`、`Retriever` 等标识符，
成本低、可解释。RRF 融合兼顾两通道的强项，且无需调权重。

### Q3: 怎么保证检索质量不退化？

12 条 golden query + CI 评测：`Recall@8` / `MRR` 异常时能立即发现。
`search --explain` 展示每条结果的 stage（vector/lexical/hybrid），
方便定位某个查询是哪个通道命中 / 失败。

### Q4: 为什么 ChromaDB 而不是 FAISS / Pinecone？

- 本地持久化、无外部服务
- 自带 metadata 过滤
- Python API 简洁
- 千级 chunk 场景不需要分布式

### Q5: 怎么保证测试可信？

- `FakeEmbedder` 用 SHA-256 生成确定性 1024 维向量
- `FakeLLMClient` 不发网络请求
- ChromaDB 走真实 `tmp_path`，测试真正的存储行为
- 201 个测试覆盖 scanner / parser / chunker / index_tracker / vector_store / retriever / services / evaluation / repository（本地 + Git bare repo）/ CLI（本地 + 远程）/ agent（Planner + CodeAgent）

---

## License

MIT
