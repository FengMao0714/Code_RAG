# Code_RAG — 代码知识库 RAG 问答助手

> 基于代码仓库的智能问答 CLI 工具，帮助你快速理解任意代码项目。

## 功能

- 🔍 **智能索引**：使用 tree-sitter 按 AST 语义切片代码（函数/类/模块级）
- 🧠 **RAG 问答**：基于向量检索 + LLM 生成，准确回答关于代码的问题
- ⚡ **增量更新**：通过文件哈希追踪，只处理变更文件
- 🌐 **多语言支持**：Python, JavaScript, TypeScript, Java, Go, Rust, C/C++ 等
- 💻 **本地优先**：Embedding 本地运行，向量库本地存储

## 技术栈

| 组件 | 技术 |
|------|------|
| CLI | typer + rich |
| 代码解析 | tree-sitter |
| 向量数据库 | ChromaDB |
| Embedding | bge-large-zh-v1.5 (本地) |
| LLM | MiMo-7B-RL (在线 API) |
| 配置管理 | pydantic-settings |

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM API Key

# 3. 索引代码仓库
code-rag index /path/to/your/repo

# 4. 提问
code-rag ask /path/to/your/repo "这个项目的核心架构是什么？"

# 5. 交互式对话
code-rag chat /path/to/your/repo
```

## CLI 命令

```bash
code-rag index <repo_path>    # 索引仓库（首次全量，后续增量）
code-rag ask <repo_path> <q>  # 单次提问
code-rag chat <repo_path>     # 交互式对话
code-rag list                 # 查看已索引的仓库
code-rag status <repo_path>   # 查看索引状态
code-rag remove <repo_path>   # 删除仓库索引
```

## 项目结构

```
src/code_rag/
├── cli.py               # CLI 入口
├── config.py            # 配置管理
├── indexer/             # 代码解析与切片
│   ├── scanner.py       # 仓库文件扫描
│   ├── parser.py        # tree-sitter 解析
│   ├── chunker.py       # 语义切片
│   └── embedder.py      # Embedding 生成
├── store/               # 向量存储
│   ├── vector_store.py  # ChromaDB 封装
│   └── index_tracker.py # 增量更新追踪
├── retriever/           # 检索
│   └── retriever.py     # 向量检索 + 上下文组装
├── generator/           # 生成
│   ├── llm.py           # LLM 调用封装
│   └── prompts.py       # Prompt 模板
└── utils/               # 工具函数
    └── file_utils.py
```

## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest tests/ -v

# 代码检查
uv run ruff check src/
uv run ruff format src/
```
