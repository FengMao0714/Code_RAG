# Code_RAG — 代码知识库 RAG 问答助手

> 基于 RAG（检索增强生成）的代码仓库问答 CLI 工具。扫描本地代码仓库，通过 AST 语义切片建立向量索引，再用自然语言提问获取关于代码的精准回答。

适合本科毕业设计和简历项目展示。

---

## 架构概览

```
代码仓库
  │
  ▼
┌─────────┐    ┌─────────┐    ┌──────────┐    ┌───────────┐
│ scanner │───▶│ parser  │───▶│ chunker  │───▶│ embedder  │
│ 文件扫描 │    │ AST 解析 │    │ 语义切片  │    │ 向量生成   │
└─────────┘    └─────────┘    └──────────┘    └───────────┘
                                                   │
                                              ┌────▼─────┐
                                              │ vector   │
                                              │ _store   │
                                              │ ChromaDB │
                                              └────┬─────┘
                                                   │
  ┌────────────────────────────────────────────┐   │
  │              用户提问 (CLI)                  │   │
  └──────────────────┬─────────────────────────┘   │
                     │                              │
              ┌──────▼──────┐    ┌───────────┐      │
              │  retriever  │◀───│ embedding  │◀─────┘
              │ 向量检索+重排 │    │ 问题向量化  │
              └──────┬──────┘    └───────────┘
                     │
              ┌──────▼──────┐
              │     LLM     │
              │  生成回答    │
              └─────────────┘
```

**索引阶段**：scanner → parser → chunker → embedder → vector_store  
**检索阶段**：问题 → embedder → retriever（向量检索 + metadata 重排 + 阈值过滤）→ LLM 生成

---

## 核心亮点

| 特性 | 说明 |
|------|------|
| **tree-sitter 多语言解析** | 支持 14 种语言：Python, JavaScript, TypeScript, Java, Go, C, C++, Rust, Ruby, PHP, C#, Swift, Lua, Bash |
| **AST 语义切片** | 按函数、类、模块语义单元切片，而非固定长度截断，保留完整代码上下文 |
| **增量索引** | 通过文件 SHA-256 哈希追踪变更，只重新处理 added/modified 文件，删除已移除文件的索引 |
| **ChromaDB 向量存储** | 本地持久化向量数据库，无需外部服务 |
| **流式 LLM 输出** | 支持 OpenAI 兼容接口的 streaming，回答逐 token 输出，体验流畅 |
| **Metadata 重排检索** | 从问题中提取标识符（文件名、函数名、类名），对匹配的 chunk 进行 boost 排序，提升召回准确率 |
| **Windows 兼容** | 解决 GBK 编码、Rich 渲染器、Braille 字符等 Windows 终端兼容问题 |
| **自动化测试** | 115 个测试用例，覆盖 scanner / parser / chunker / index_tracker / vector_store / retriever / CLI |

---

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装

```bash
# 克隆仓库
git clone https://github.com/your-username/Code_RAG.git
cd Code_RAG

# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM API Key 和 Base URL
```

### 索引与问答

```bash
# 索引代码仓库（首次全量，后续增量）
uv run code-rag index /path/to/your/repo

# 单次提问
uv run code-rag ask /path/to/your/repo "这个项目的核心架构是什么？"

# 交互式对话（输入 exit 退出）
uv run code-rag chat /path/to/your/repo
```

---

## CLI 命令

```bash
uv run code-rag index <repo_path>         # 索引仓库（首次全量，后续增量）
uv run code-rag ask <repo_path> "<问题>"   # 单次提问
uv run code-rag chat <repo_path>          # 交互式对话
uv run code-rag search <repo_path> "<问题>" # 检索调试（只显示召回结果，不调用 LLM）
uv run code-rag list                      # 查看已索引的仓库
uv run code-rag status <repo_path>        # 查看索引状态（切片数、类型分布）
uv run code-rag remove <repo_path>        # 删除仓库索引
uv run code-rag remove <repo_path> --yes  # 删除索引（跳过确认）
```

---

## 项目结构

```
src/code_rag/
├── cli.py               # CLI 入口 (typer app)
├── config.py            # 配置管理 (pydantic-settings, .env)
├── indexer/
│   ├── scanner.py       # 仓库文件扫描 + .gitignore 过滤
│   ├── parser.py        # tree-sitter 多语言 AST 解析
│   ├── chunker.py       # 语义切片（函数/类/模块/文档）
│   └── embedder.py      # 本地 Embedding 生成 (bge-large-zh-v1.5)
├── store/
│   ├── vector_store.py  # ChromaDB 封装（upsert / query / delete）
│   └── index_tracker.py # 增量更新追踪（文件哈希对比）
├── retriever/
│   └── retriever.py     # 向量检索 + metadata boost 重排 + 上下文组装
├── generator/
│   ├── llm.py           # LLM 调用封装（OpenAI SDK, 流式输出）
│   └── prompts.py       # System/User Prompt 模板
└── utils/
    └── __init__.py

tests/
├── conftest.py           # 共享 fixtures（FakeEmbedder, FakeLLMClient）
├── test_scanner.py       # 21 个测试
├── test_parser_chunker.py # 18 个测试
├── test_index_tracker.py  # 9 个测试
├── test_vector_store.py   # 12 个测试
├── test_retriever.py      # 28 个测试
└── test_cli.py            # 8 个测试（CLI smoke 测试）
```

---

## 测试

```bash
# 运行全部测试
uv run pytest tests/ -v

# 静默模式
uv run pytest -q

# 运行单个测试文件
uv run pytest tests/test_scanner.py -v
```

### 测试覆盖

| 模块 | 测试数 | 覆盖要点 |
|------|--------|---------|
| scanner | 21 | 目录忽略、扩展名过滤、语言检测、SHA-256 哈希、.gitignore、跨平台路径 |
| parser + chunker | 18 | Python class/function 解析、空文件容错、4 种 chunk 类型生成、metadata 完整性 |
| index_tracker | 9 | 全量 added、增量 modified/deleted、无变更幂等、跨实例持久化 |
| vector_store | 12 | upsert/query、幂等写入、阈值过滤、delete_by_files、空 collection 容错 |
| retriever | 28 | 关键词提取、metadata boost 重排、上下文格式化、集成检索流程 |
| CLI | 8 | --help、status、index、ask、list 命令 smoke 测试 |
| **总计** | **115** | |

测试设计原则：
- **零网络依赖**：不发起任何 HTTP 请求，LLM 用 FakeLLMClient 模拟
- **零模型下载**：Embedding 用 SHA-256 确定性向量模拟，不加载 sentence-transformers
- **真实 ChromaDB**：使用 pytest `tmp_path` 临时目录，测试真实向量数据库行为

### 代码检查

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| CLI | typer + rich |
| 代码解析 | tree-sitter（14 种语言 grammar） |
| 向量数据库 | ChromaDB（本地持久化） |
| Embedding | BAAI/bge-large-zh-v1.5（本地运行） |
| LLM | OpenAI 兼容接口（默认 MiMo-7B-RL） |
| 配置管理 | pydantic-settings |
| Token 计数 | tiktoken |
| 包管理 | uv + hatchling |

---

## 已知限制

| 限制 | 说明 |
|------|------|
| `chat` 为单轮模式 | 每轮独立检索 + 生成，无对话历史上下文 |
| Embedding 模型首次加载慢 | bge-large-zh-v1.5 约 1.3GB，首次运行需下载 |
| Token 计数器与 Embedding 模型不完全对齐 | chunker 使用 tiktoken (cl100k_base) 计数，Embedding 模型使用自有 tokenizer，`max_chunk_tokens=512` 为近似值 |
| 语法错误文件处理 | tree-sitter 容错解析不崩溃，但错误代码中的符号可能被索引 |
| `list` 命令显示信息有限 | 当前只显示仓库哈希和切片数，尚未显示原始路径 |

---

## 面试 / 答辩讲解角度

### Q1: 为什么不用固定长度切片？

固定长度切片（如每 512 token 一刀切）会把函数、类从中间截断，导致语义不完整。
本项目按 AST 语义单元切片：一个函数是一个 chunk，一个类是一个 chunk。
这样检索到的代码片段具有完整语义，LLM 能基于完整上下文生成准确回答。

### Q2: 为什么需要增量索引？

每次代码变更后全量重建索引代价高（重新解析所有文件、重新生成 Embedding、重新写入向量库）。
本项目通过文件 SHA-256 哈希追踪变更，只对 added + modified 文件重新处理，deleted 文件从索引中移除。
实测：对 37 个文件的仓库，无变更时 `index` 命令秒级完成。

### Q3: 如何提升召回质量？

1. **Metadata boost 重排**：从问题中提取文件名、函数名等标识符，匹配 chunk 的 `file_path`/`name`/`parent`，相关 chunk 排到前面
2. **扩大候选池**：ChromaDB 返回 `top_k * 5` 个候选（最少 50），再经 metadata 重排和阈值过滤，避免高相关但向量距离稍远的 chunk 被截断
3. **阈值回退保底**：当阈值过滤后无结果时，自动放宽条件重查，确保至少返回 top_k 条结果

### Q4: 为什么选 ChromaDB 而非 FAISS/Pinecone？

- ChromaDB 支持本地持久化，无需外部服务部署
- 自带 metadata 过滤能力，配合 boost 重排
- Python API 简洁，适合项目原型开发
- 毕设场景下数据量（千级 chunk）不需要分布式方案

---

## License

MIT
