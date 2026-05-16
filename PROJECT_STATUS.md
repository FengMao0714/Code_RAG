# PROJECT_STATUS.md — Code_RAG 项目状态

> 最后更新：2026-05-16（第一阶段修复完成）

## 项目概述

代码知识库 RAG 问答助手，CLI 工具。核心流程：

```
代码仓库 -> tree-sitter AST 解析 -> 语义切片 -> Embedding -> ChromaDB -> 向量检索 -> LLM 生成回答
```

---

## 第一阶段修复记录（2026-05-16）

### 修复的问题

#### 1. Windows 默认终端 GBK 下 CLI 崩溃（高优先级）

**现象**：在 Windows cmd/PowerShell 默认编码（GBK）下执行任何 CLI 命令，Rich 渲染器输出 Braille 字符或 emoji 时触发 `UnicodeEncodeError: 'gbk' codec can't encode character`，程序直接崩溃。

**根因**：
- Rich 15.0.0 检测到 Windows 终端后使用 `LegacyWindowsTerm` 渲染器，直接调用 Win32 API 写入控制台，绕过 Python I/O 编码层
- Rich 的 `SpinnerColumn` 默认使用 Braille 字符（U+2800-U+28FF）做动画，GBK 无法编码
- CLI 输出中包含 emoji 和 Unicode 项目符号（U+2022），GBK 同样无法编码

**修复方案**（三管齐下）：
- 模块顶部 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")` — 让 Python I/O 层输出 UTF-8
- `console.legacy_windows = False` — 强制 Rich 走现代渲染器路径（通过 Python I/O 而非 Win32 API）
- `SpinnerColumn(spinner_name="line")` — 用纯 ASCII spinner 替代 Braille 字符
- 去掉 CLI 输出中所有 emoji，用 `>>` / `[OK]` 替代；项目符号替换为 `-`

#### 2. ChromaDB collection 不存在时异常（高优先级）

**现象**：对未索引仓库执行 `code-rag status .` 或 `code-rag remove .` 时抛出异常崩溃。ChromaDB >= 0.5 抛出 `chromadb.errors.NotFoundError`，代码只捕获了 `ValueError`。

**根因**：`vector_store.py` 的 `delete_collection` 和 `get_stats` 方法中 `except ValueError` 无法匹配 ChromaDB 新版的 `NotFoundError`。

**修复方案**：
- 顶部兼容导入 `from chromadb.errors import NotFoundError`（失败时回退到 `ValueError`）
- 两处 `except ValueError` 改为 `except _ChromaNotFoundError`

#### 3. 检索阈值过高导致相关代码被过滤（高优先级）

**现象**：问"CLI 入口在哪里"，`src/code_rag/cli.py` 的检索距离分数为 0.529，被默认阈值 0.5 过滤掉，LLM 只能看到文档类 chunk，答错。

**根因**：
- 默认阈值 `retrieval_score_threshold = 0.5` 过于严格
- `retriever.py` 中 `score_threshold or ...` 使用 `or` 运算符，当显式传入 `0.0` 时会被错误覆盖为默认值
- 无保底策略：阈值过滤后可能返回空结果

**修复方案**：
- 默认阈值 `0.5` -> `0.7`（`config.py` + `.env` + `.env.example`）
- `or` 运算符改为 `is not None` 检查
- 新增保底回退：阈值过滤后无结果时，自动放宽 `max_distance=None` 重查，确保至少返回 top_k 条

#### 4. 项目配置文件未被索引（高优先级）

**现象**：`pyproject.toml`、`package.json` 等项目配置文件不入索引，导致问"CLI 入口在哪里"时缺少 `[project.scripts]` 的关键证据。

**根因**：`scanner.py` 的 `DOC_EXTENSIONS` 只包含 `.md/.rst/.txt/.adoc`，不包含配置文件扩展名。

**修复方案**：`DOC_EXTENSIONS` 增加 `.toml/.yaml/.yml/.json`，这些文件作为 `doc` chunk 直接入库。

---

### 修改的文件

| 文件 | 改动摘要 | 改动量 |
|---|---|---|
| `src/code_rag/cli.py` | stdout/stderr UTF-8 重编码；`legacy_windows=False`；ASCII spinner；去掉所有 emoji 和 Unicode 符号 | ~15 行 |
| `src/code_rag/store/vector_store.py` | 兼容导入 `NotFoundError`；两处 `except` 精确捕获 | ~8 行 |
| `src/code_rag/retriever/retriever.py` | `or` 改为 `is not None`；新增阈值回退保底策略 | ~15 行 |
| `src/code_rag/config.py` | `retrieval_score_threshold` 默认值 0.5 -> 0.7 | 1 行 |
| `src/code_rag/indexer/scanner.py` | `DOC_EXTENSIONS` 增加 `.toml/.yaml/.yml/.json` | 4 行 |
| `.env` | `RETRIEVAL_SCORE_THRESHOLD=0.5` -> `0.7` | 1 行 |
| `.env.example` | 同上 | 1 行 |

### 验证命令结果

```
$ uv run --frozen ruff check src/
All checks passed!

$ uv run --frozen code-rag status .
>> 仓库索引状态: E:\code\Code_RAG
  Collection: code-rag-31cec114b63b
  总切片数: 263
  切片类型分布:
    - doc: 74
    - module_summary: 21
    - function: 139
    - class: 29

$ uv run --frozen code-rag index .
>> 开始索引仓库: E:\code\Code_RAG
  扫描完成: 37 个文件（修复前 36 个，新增 .toml 等配置文件）
  变更检测: +1 ~5 -0
  Embedding 完成: 70 个向量
  写入完成
  [OK] 索引完成！处理 6 个文件，生成 70 个切片

$ uv run --frozen code-rag ask . "这个项目的 CLI 入口在哪里？"
  检索到 8 条结果 (阈值=0.70)    <-- 修复前：0 条（阈值 0.5 过滤掉）
  LLM 回答：项目的 CLI 入口文件是 src/code_rag/cli.py
             引用了 pyproject.toml 的 [project.scripts] 配置  <-- 修复前无法引用
```

---

## 当前剩余问题

### 高优先级

| 问题 | 说明 |
|---|---|
| 没有自动化测试 | `tests/` 目录为空，`pytest` -> `no tests ran`。简历展示最大短板。 |
| `list` 命令不显示仓库原始路径 | 只显示 hash 和文件数，无法知道是哪个仓库。 |

### 中优先级

| 问题 | 说明 |
|---|---|
| `chat` 是循环单轮问答 | 没有保留对话历史，每轮都是独立检索 + 生成。 |
| parser 不检查语法错误 | 有语法错误的文件不崩溃，但错误代码也会被解析出符号，可能污染索引。 |
| class chunk 可能保留方法体 | 设计文档说"不含方法体"，但实现中 `_chunk_class` 的跳过逻辑不完整。 |
| ChromaDB `extra_metadata` 丢失 | upsert 时存了 `extra_metadata` JSON 字符串，但 query 重建 `CodeChunk` 时未恢复。 |
| `.env` 中硬编码了真实 API Key | 应该移到 `.env.local` 并加入 `.gitignore`。 |

### 低优先级

| 问题 | 说明 |
|---|---|
| README 与实际文件不一致 | 提到 `utils/file_utils.py` 但实际不存在。 |
| 首次加载 Embedding 模型 HF 日志过多 | CLI 体验偏吵，可降低 logging level。 |
| `list` 显示体验一般 | 只显示仓库 hash 和文件数，建议显示原始路径和最后索引时间。 |
| prompts.py 中有 emoji | `CONTEXT_CHUNK_TEMPLATE` 中的 emoji 不会直接输出到终端（传给 LLM），但 LLM 回传时可能触发编码问题。 |
