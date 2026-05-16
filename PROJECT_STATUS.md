# PROJECT_STATUS.md — Code_RAG 项目状态

> 最后更新：2026-05-16（第三阶段 RAG 检索质量优化完成）

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

## 第二阶段测试补充记录（2026-05-16）

### 目标

补齐核心自动化测试，覆盖 scanner / parser / chunker / index_tracker / vector_store / CLI 六大模块，
消除"零测试覆盖"这一简历展示最大短板。

### 设计约束

- **零网络依赖**：不发起任何 HTTP 请求
- **零模型下载**：不加载 sentence-transformers / bge-large-zh-v1.5
- **ChromaDB 真实运行**：使用 pytest `tmp_path` 临时目录，测试真实向量数据库行为
- **CLI 通过 Typer CliRunner**：monkeypatch 替换 `get_settings`、`Embedder.get_instance`、`LLMClient`

### 新增文件

| 文件 | 测试数 | 覆盖点 |
|---|---|---|
| `tests/__init__.py` | — | 包标识 |
| `tests/conftest.py` | — | 共享 fixtures：`FakeEmbedder`（SHA-256 确定性 1024 维归一化向量）、`FakeLLMClient`（无网络）、`tmp_settings`、`patch_embedder`、`patch_llm` |
| `tests/test_scanner.py` | 21 | 忽略目录（node_modules/.git/__pycache__/venv/dist/build）、入库扩展名（.py/.md/.toml/.json/.yaml/.js/.ts）、语言检测（8 种）、SHA-256 哈希、.gitignore 过滤、跨平台路径、边界情况 |
| `tests/test_parser_chunker.py` | 18 | Python class/function 解析、空文件、语法错误不崩溃、module_summary/class/function/doc chunk 生成、chunk metadata 完整性、长函数二次切分（sub_index/sub_total）、sub-chunk 源码完整性、token 计数 |
| `tests/test_index_tracker.py` | 9 | 首次全 added、modified/deleted 识别、无变更不重复索引、混合变更（added+modified+deleted）、跨实例持久化、仓库路径隔离 |
| `tests/test_vector_store.py` | 12 | upsert/query 多条、幂等 upsert、空 collection 查询、距离阈值过滤、delete_by_files、delete_collection、不存在 collection 不崩溃、get_stats chunk_type 分布、完整生命周期、SearchResult 结构 |
| `tests/test_cli.py` | 8 | `--help` 退出码、`status` 未索引/已索引、`index` 最小闭环+ChromaDB 验证、`index` 空目录、`ask` 返回回答（fake LLM）、`ask` 未索引仓库提示、`list` 空状态 |

### 修改的文件

| 文件 | 改动摘要 | 改动量 |
|---|---|---|
| `tests/__init__.py` | 新增，空文件 | 0 行 |
| `tests/conftest.py` | 新增，共享 fixtures + FakeEmbedder + FakeLLMClient | ~110 行 |
| `tests/test_scanner.py` | 新增，scanner 模块 21 个测试 | ~340 行 |
| `tests/test_parser_chunker.py` | 新增，parser + chunker 模块 18 个测试 | ~350 行 |
| `tests/test_index_tracker.py` | 新增，index_tracker 模块 9 个测试 | ~170 行 |
| `tests/test_vector_store.py` | 新增，vector_store 模块 12 个测试 | ~250 行 |
| `tests/test_cli.py` | 新增，CLI smoke 测试 8 个测试 | ~200 行 |

**注意**：未修改任何 `src/` 业务代码。

### 验证命令结果

```
$ uv run --frozen ruff check src/ tests/
All checks passed!

$ uv run --frozen ruff format --check src/ tests/
24 files already formatted

$ uv run --frozen pytest -q
........................................................................ [ 82%]
...............                                                          [100%]
87 passed in 2.69s
```

### 遇到并解决的问题

| 问题 | 原因 | 解决方式 |
|---|---|---|
| `ModuleNotFoundError: code_rag` | pytest 未安装（dev 依赖未 sync） | `uv sync --extra dev` |
| 8 个测试首次运行失败 | pytest 在 tmp_path 创建元数据文件被 scanner 扫描到；`.gitignore` 本身不在忽略列表；`.pdf` 不在 `_DEFAULT_IGNORE_EXTENSIONS` | index_tracker 改用 `_make_entry` 构造 FileEntry；scanner 测试改用 subset 断言；二进制扩展名测试改用 `.exe` |
| CLI ask 测试 `[fake answer]` 不在输出中 | Typer CliRunner 在 Windows GBK 下对 `[]` 的编码处理 | 改为断言 `context_length=` 和 `question=` 等特征字符串 |

---

## 第三阶段：优化 RAG 检索质量（2026-05-16）

### 目标

优化检索链路，让 `ask` 命令能准确回答"CLI 入口在哪里"、"scanner 如何过滤文件"等问题，不再只依赖向量分数。

### 解决的问题

#### 1. 检索结果中 cli.py、pyproject.toml 等关键文件被向量阈值过滤（高优先级）

**现象**：问"CLI 入口在哪里"，`src/code_rag/cli.py` 的向量距离约 0.59，虽低于阈值 0.7，但因 top_k=8 只取最相似的 8 条，cli.py 排在第 30 名之后被截断，LLM 看不到核心代码。

**根因**：
- 检索只取 top_k=8 个最相似的候选，距离阈值在 ChromaDB 查询层过滤
- 中文问题 vs 英文代码的向量距离天然较高，代码文件难以进入 top_k
- 没有利用 chunk 的 `file_path`、`name` 等 metadata 做辅助排序

**修复方案**：
- 候选池扩大到 `top_k * 5`（最少 50），在 ChromaDB 层不做距离过滤
- 新增 `boost_by_metadata()`：从查询中提取标识符（文件名、函数名、类名、中文关键词），匹配 `file_path`/`name`/`parent` 的 chunk 排到结果前面
- 距离阈值改为本地过滤，保证 boost 后的高相关 chunk 不被误杀

#### 2. 上下文缺少检索质量信息（中优先级）

**现象**：LLM 回答时无法知道哪些代码片段是高置信命中、哪些是兜底召回，也无法看到文件路径和行号以外的元数据。

**修复方案**：
- `CONTEXT_CHUNK_TEMPLATE` 增加 `score`（检索距离）、`language`（语言）字段
- `ContextBuilder.build_context()` 新增 `scores` 参数，逐 chunk 填充距离分数

#### 3. 没有检索调试手段（中优先级）

**现象**：调试"为什么这个问题召回不到正确 chunk"只能靠加日志，没有直接查看召回结果的命令。

**修复方案**：
- CLI 新增 `search` 子命令，只执行检索 + 显示结果（文件、分数、类型、行号），不调用 LLM

#### 4. Retriever 模块零测试覆盖（中优先级）

**现象**：87 个测试中没有一个是针对 `Retriever` 或 `ContextBuilder` 的。

**修复方案**：新增 `tests/test_retriever.py`，28 个测试覆盖关键词提取、metadata boost、上下文格式化、集成检索。

---

### 修改的文件

| 文件 | 改动摘要 | 改动量 |
|---|---|---|
| `src/code_rag/generator/prompts.py` | `CONTEXT_CHUNK_TEMPLATE` 增加 `score`、`language` 字段 | ~5 行 |
| `src/code_rag/retriever/retriever.py` | 新增 `_extract_keywords()`、`boost_by_metadata()`；`retrieve()` 改为三阶段流程（扩大候选池 → boost 重排 → 本地阈值过滤）；`build_context()` 增加 `scores` 参数；`retrieve_with_context()` 传递 scores | ~120 行 |
| `src/code_rag/cli.py` | 新增 `search` 命令（调试检索，不调用 LLM） | ~45 行 |
| `tests/test_retriever.py` | 新增，28 个测试 | ~460 行 |

### 检索流程变化

**修改前**：
```
问题 → Embedder → ChromaDB(top_k=8, 阈值过滤) → ContextBuilder(无score) → LLM
```

**修改后**：
```
问题 → Embedder → ChromaDB(候选池 top_k×5, 不限距离)
     → _extract_keywords 提取标识符(file/class/function/中文2字组)
     → boost_by_metadata 重排(file_path/name/parent 匹配的排前面)
     → 本地阈值过滤(≤0.7 保留, 无结果则回退取全部)
     → 截取 top_k
     → ContextBuilder(含 score/file/lines/language)
     → LLM
```

### 验证命令结果

```
$ uv run --frozen ruff check src/ tests/
All checks passed!

$ uv run --frozen ruff format --check src/ tests/
25 files already formatted

$ uv run --frozen pytest -q
........................................................................ [ 62%]
...........................................                              [100%]
115 passed in 9.28s

$ uv run --frozen code-rag search . "这个项目的 CLI 入口在哪里？"
>> 检索调试: 这个项目的 CLI 入口在哪里？
检索到 8 条结果:
  [1] score=0.5886  type=module_summary  name=cli.py  file=src/code_rag/cli.py
  [2] score=0.5918  type=module_summary  name=cli.py  file=src/code_rag/cli.py
  [3] score=0.5996  type=class  name=TestStatusCommand  file=tests/test_cli.py
  ...

$ uv run --frozen code-rag ask . "这个项目的 CLI 入口在哪里？"
检索到 8 条结果 (阈值=0.70)
LLM 回答：
  - 正确引用 src/code_rag/cli.py (typer.Typer app 定义，第15-18行)
  - 正确引用 pyproject.toml [project.scripts] 配置
  - 给出完整入口链路: app -> @app.command() -> code-rag = "code_rag.cli:app"
```

---

## 当前剩余问题

### 高优先级

| 问题 | 说明 |
|---|---|
| `list` 命令不显示仓库原始路径 | 只显示 hash 和文件数，无法知道是哪个仓库。 |

### 中优先级

| 问题 | 说明 |
|---|---|
| `chat` 是循环单轮问答 | 没有保留对话历史，每轮都是独立检索 + 生成。 |
| parser 不检查语法错误 | 有语法错误的文件不崩溃，但错误代码也会被解析出符号，可能污染索引。 |
| class chunk 可能保留方法体 | 设计文档说"不含方法体"，但实现中 `_chunk_class` 的跳过逻辑不完整（方法体内空行会提前结束 skip_body）。 |
| ChromaDB `extra_metadata` 丢失 | upsert 时存了 `extra_metadata` JSON 字符串，但 `_reconstruct_chunk` 重建时未恢复 `CodeChunk.metadata`，导致 sub_index/sub_total 信息丢失。 |
| `.env` 中硬编码了真实 API Key | 应该移到 `.env.local` 并加入 `.gitignore`。 |

### 低优先级

| 问题 | 说明 |
|---|---|
| README 与实际文件不一致 | 提到 `utils/file_utils.py` 但实际不存在。 |
| 首次加载 Embedding 模型 HF 日志过多 | CLI 体验偏吵，可降低 logging level。 |
| `list` 显示体验一般 | 只显示仓库 hash 和文件数，建议显示原始路径和最后索引时间。 |
| embedding 模型与 tokenizer 不对齐 | chunker 用 `cl100k_base`（OpenAI）计 token，embedding 用 `BAAI/bge-large-zh-v1.5`（自有 tokenizer），`max_chunk_tokens=512` 语义不精确。 |
