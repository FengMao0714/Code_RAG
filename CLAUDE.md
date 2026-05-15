# CLAUDE.md — Code_RAG 项目规范

> 本文件供 Claude Code 阅读，定义项目的架构约束和编码规范。

## 项目概述

Code_RAG 是一个 **代码知识库 RAG 问答助手**，CLI 工具，帮助用户快速理解任意代码仓库。

**核心流程**：代码仓库 → tree-sitter AST 解析 → 语义切片 → Embedding → ChromaDB → 向量检索 → LLM 生成回答

## 技术栈

- **Python 3.11+**，包管理使用 `uv`
- **CLI**：`typer` + `rich`
- **代码解析**：`tree-sitter` + `tree-sitter-languages`
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
├── retriever/
│   └── retriever.py     # 向量检索 + 上下文组装
├── generator/
│   ├── llm.py           # LLM 调用封装 (OpenAI SDK)
│   └── prompts.py       # System/User Prompt 模板
└── utils/
    └── file_utils.py    # 文件读写、路径处理等工具函数
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
code-rag index <repo_path>         # 索引（增量）
code-rag ask <repo_path> "<question>"  # 单次问答
code-rag chat <repo_path>          # 交互模式
code-rag list                      # 列出已索引仓库
code-rag status <repo_path>        # 索引状态
code-rag remove <repo_path>        # 删除索引
```

## 开发命令

```bash
# 安装依赖
uv sync

# 运行
uv run code-rag --help

# 测试
uv run pytest tests/ -v

# 代码检查
uv run ruff check src/
uv run ruff format src/
```

## 重要提醒

1. **不要修改 CLAUDE.md** 的核心约束，除非用户明确要求
2. 每次修改代码后，确保 `uv run ruff check src/` 通过
3. 新增模块/函数时，同步更新对应的测试文件
4. Embedding 模型是本地运行的，首次使用会自动下载（约 1.3GB）
