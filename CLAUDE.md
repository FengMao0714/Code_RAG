# CLAUDE.md — Code_RAG 项目规范

> 本文件供 Claude Code 阅读，定义项目的架构约束和编码规范。

## 项目概述

Code_RAG 是一个 **代码知识库 RAG 问答助手**，CLI 工具，帮助用户快速理解任意代码仓库。

**核心流程**：代码仓库 → tree-sitter AST 解析 → 语义切片 → Embedding → ChromaDB → 向量检索 → LLM 生成回答

## 技术栈

- **Python 3.11+**，包管理使用 `uv`
- **CLI**：`typer` + `rich`
- **代码解析**：`tree-sitter` + 各语言独立 grammar 包（如 `tree-sitter-python`）
- **向量数据库**：`ChromaDB`（本地持久化）
- **Embedding**：`sentence-transformers` + `BAAI/bge-large-zh-v1.5`（本地模型）
- **LLM**：通过 OpenAI 兼容接口调用在线 API（默认 MiMo-7B-RL）
- **配置**：`pydantic-settings`

## 目录结构

```
src/code_rag/
├── cli.py               # CLI 入口 (typer app)
├── config.py            # Settings 类 (pydantic-settings，从 .env 加载)
├── indexer/
│   ├── scanner.py       # 仓库文件扫描 + .gitignore 过滤
│   ├── parser.py        # tree-sitter 多语言 AST 解析
│   ├── chunker.py       # 语义切片（函数/类/模块/文档）
│   └── embedder.py      # 本地 Embedding 生成
├── store/
│   ├── vector_store.py  # ChromaDB 封装（CRUD + upsert）
│   └── index_tracker.py # 增量更新追踪（文件哈希对比）
├── repository/          # 仓库源抽象（本地路径 / Git URL）
│   ├── models.py        # RepoSource / RepoIdentity / ResolvedRepo
│   ├── parser.py        # 本地路径 / Git URL 解析
│   ├── local.py         # 本地路径 provider
│   ├── git.py           # Git clone / fetch / checkout
│   ├── cache.py         # 远程仓库缓存目录
│   └── resolver.py      # resolve_repo 统一入口
├── retriever/
│   ├── retriever.py     # 向量检索 + metadata boost + 上下文组装
│   ├── lexical.py       # 词法检索（符号/文件/源码子串）
│   ├── rerank.py        # RRF 重排序（k=60，通道权重）
│   └── hybrid.py        # 向量 + 词法 + RRF 融合
├── generator/
│   ├── llm.py           # LLM 调用封装 (OpenAI SDK)
│   └── prompts.py       # System/User Prompt 模板
├── services/            # 业务编排层（CLI 不再做业务）
│   ├── index_service.py
│   ├── query_service.py
│   ├── manifest_service.py
│   └── eval_service.py
├── evaluation/          # 检索评测
│   ├── dataset.py
│   ├── metrics.py
│   └── report.py
├── agent/               # 轻量 Code Agent（只读）
│   ├── models.py
│   ├── planner.py
│   └── code_agent.py
└── utils/
    └── __init__.py      # 预留工具包
```

## 编码规范

### 1. 类型注解
- 所有函数必须有完整的类型注解（参数 + 返回值）
- 使用 Python 3.11+ 语法：`list[str]` 而非 `List[str]`，`str | None` 而非 `Optional[str]`

### 2. Docstring
- 每个模块、类、公开函数都必须有 docstring
- 使用 Google 风格 docstring

### 3. 日志
- 使用 `logging` 模块，不要 `print()`
- logger 命名：`logger = logging.getLogger(__name__)`

### 4. 错误处理
- 不要吞异常，至少 `logger.error()` 记录
- 对外的函数返回有意义的错误信息，不要抛裸异常

### 5. 配置管理
- 所有可配置项通过 `config.py` 的 `Settings` 类管理
- 从 `.env` 文件读取，使用 `pydantic-settings`
- 禁止在代码中硬编码 API Key、路径等

## 核心设计约束

### 切片策略（chunker.py）
切片必须按 **语义单元**，不允许按固定字数暴力切割：

| 切片类型 | 描述 | 示例 |
|---------|------|------|
| `module_summary` | 文件级概要：路径 + imports + 顶层变量 | 每个文件一个 |
| `class` | 类定义 + docstring + 方法签名列表 | 不含方法体 |
| `function` | 完整函数/方法代码 | 含 docstring |
| `doc` | 文档文件内容 | README, .md 文件 |

### Chunk Metadata 结构
每个 chunk 必须包含以下 metadata：

```python
{
    "file_path": str,      # 相对于仓库根目录的路径
    "language": str,       # 编程语言
    "chunk_type": str,     # module_summary / class / function / doc
    "name": str,           # 函数/类名，或文件名
    "start_line": int,     # 起始行号
    "end_line": int,       # 结束行号
    "parent": str | None,  # 所属类名（方法时）
    "file_hash": str,      # 文件 SHA256 哈希（用于增量更新）
}
```

### 增量更新流程（index_tracker.py）
1. 扫描仓库所有文件 → 计算 SHA256 哈希
2. 对比 `tracker.json` 中记录的上次哈希
3. 分为三类：`added`（新文件）、`modified`（哈希变化）、`deleted`（文件不存在了）
4. 只对 added + modified 文件重新解析、切片、embedding、入库
5. 对 deleted 文件的 chunks 从 ChromaDB 中删除
6. 更新 `tracker.json`

### LLM 调用（llm.py）
- 使用 `openai` SDK 的 `OpenAI` 客户端
- 通过 `base_url` 配置指向在线 API
- 必须支持 **流式输出**（streaming）

### CLI 命令（cli.py）
```bash
# 同一组命令同时接受本地路径与 Git 仓库 URL
code-rag index  <source> [--ref <git-ref>] [--refresh]   # 索引（增量）
code-rag ask    <source> "<question>"  [--ref <git-ref>]  # 单次问答
code-rag chat   <source> [--ref <git-ref>]               # 交互模式
code-rag search <source> "<question>"                    # 检索调试（不调用 LLM）
code-rag list                                            # 列出已索引仓库
code-rag status <source> [--ref <git-ref>]               # 索引状态
code-rag remove <source> --yes [--with-cache]            # 删除索引（可选同步删远程缓存）
code-rag eval   <source> --dataset <yaml> [--mode ...]   # 检索评测（不调用 LLM）
code-rag agent  <source> "<task>" [--plan-only]          # 轻量 Code Agent（只读）
code-rag cache list                                      # 列出所有远程仓库缓存
code-rag cache prune --yes                               # 清理所有远程仓库缓存
```

## 开发命令

```bash
# 安装依赖
uv sync

# 运行
uv run code-rag --help

# 测试
uv run --frozen pytest tests/ -v

# 代码检查
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
```

## 重要提醒

1. **不要修改 CLAUDE.md** 的核心约束，除非用户明确要求
2. 每次修改代码后，确保 `uv run ruff check src/` 通过
3. 新增模块/函数时，同步更新对应的测试文件
4. Embedding 模型是本地运行的，首次使用会自动下载（约 1.3GB）

## AI/RAG 简历优化重构规则

本项目当前目标是把已有 Code_RAG 打磨成适合 AI/RAG 实习简历展示的工程项目。不要把任务理解成从零重写，也不要为了炫技引入偏离主线的大型框架。

### 执行原则

1. 先阅读真实代码、测试、README、PROJECT_STATUS，再开始修改。
2. 以真实代码为准；如果文档与代码不一致，修正文档或在实现中保持向后兼容。
3. 保留现有 CLI RAG 闭环：`index -> search/ask/chat -> status/list/remove`。
4. 不要大规模推倒重写核心链路，优先做分层、评测、检索增强和展示打磨。
5. 不要新增 Web UI、FastAPI 或前端页面，除非用户重新明确要求。
6. 不要让测试依赖真实网络、真实 LLM API 或模型下载。
7. 不要提交 `.env`、API Key、本地 ChromaDB 数据、模型缓存和临时 debug 文件。
8. 若需要新增依赖，必须说明原因，并保持 `uv run --frozen` 可复现。

### 重构优先级

最高优先级：

1. 服务层重构：把 `cli.py` 的业务编排拆到 `services/`，CLI 只负责参数解析和 Rich 展示。
2. 仓库 Manifest：记录真实仓库路径、collection、最后索引时间、文件数、chunk 数、模型和关键配置。
3. Hybrid Retrieval：在现有向量检索和 metadata boost 基础上，增加符号/文件名词法召回，并用 RRF 融合排序。
4. Retrieval Eval：增加 golden query 数据集、Recall@k、MRR、JSON/Markdown 报告和 `code-rag eval` 命令。
5. README 展示：补充架构、评测结果、核心亮点、演示命令和可直接写进简历的项目描述。

中等优先级：

1. `search --explain` 展示 vector、lexical、rerank 的命中来源和阶段耗时。
2. 低置信度策略：无证据或弱证据时明确提示，不诱导 LLM 硬答。
3. GitHub Actions：至少运行 ruff check、format check、pytest。

低优先级：

1. Web/API/UI。
2. 替换 ChromaDB 或 embedding 模型。
3. 无明确收益的复杂设计模式。

### 推荐新增模块

```text
src/code_rag/services/
├── __init__.py
├── index_service.py
├── query_service.py
└── eval_service.py

src/code_rag/evaluation/
├── __init__.py
├── dataset.py
├── metrics.py
└── report.py

src/code_rag/retriever/
├── lexical.py
└── rerank.py

src/code_rag/store/
└── manifest.py

evals/
└── code_rag_golden.yaml
```

### 推荐公开命令

```bash
uv run code-rag search . "CLI 入口在哪里" --mode hybrid --explain
uv run code-rag search . "CLI 入口在哪里" --mode vector
uv run code-rag search . "CLI 入口在哪里" --mode lexical
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --output reports/eval_latest.json --markdown reports/eval_latest.md
```

### Golden Query 要求

`evals/code_rag_golden.yaml` 至少包含 10 条问题，覆盖：

1. 符号定位：CLI 入口在哪里。
2. 配置定位：项目脚本入口在哪里定义。
3. scanner 如何过滤文件。
4. 增量索引如何判断 added/modified/deleted。
5. chunker 如何处理超长函数。
6. retriever 如何做 metadata boost。
7. ChromaDB 如何 upsert 和 query。
8. LLM 流式输出在哪里实现。
9. 负样本：项目是否实现了 Web UI。
10. 歧义样本：`list`、`status`、`remove` 各自做什么。

每条 golden query 应包含：

```yaml
- id: cli_entry
  question: CLI 入口在哪里？
  expected_files:
    - src/code_rag/cli.py
    - pyproject.toml
  expected_symbols:
    - app
```

### 评测指标

Retrieval eval 至少输出：

- Recall@1
- Recall@3
- Recall@8
- MRR
- expected file hit
- expected symbol hit
- 每条 query 的检索耗时
- 每条失败样例的命中文件和排名

`eval` 命令不得调用 LLM，只评估 retrieval。

### 最终验收命令

每个阶段结束后必须运行：

```bash
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
```

最终还必须运行：

```bash
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8
```

### 最终交付说明

完成重构后，必须给出：

1. 改动摘要。
2. 新增文件和模块。
3. 新增测试与评测数据集。
4. 所有验证命令结果。
5. Retrieval eval 指标摘要。
6. README 中可用于简历的一句话项目描述。

### 推荐简历表达

开发代码仓库 RAG 问答 CLI，基于 tree-sitter 实现 14 种语言 AST 语义切片，结合 BGE Embedding、ChromaDB、符号词法索引与 RRF 混合召回，支持增量索引、流式问答和可复现检索评测；构建 golden query 评测集统计 Recall@k/MRR，并通过 100+ 自动化测试和 Windows/Linux CI 保障质量。
