# Claude Execution Log

执行时间：2026-06-11

执行模型：minimal-change-engineer 子代理

执行依据：`docs/agent-handoff/02-implementation-plan.md`

---

## 最终验收结果

| 命令 | 结果 |
|------|------|
| `uv run --frozen pytest -q` | 246 passed |
| `uv run --frozen ruff check src/ tests/` | All checks passed |
| `uv run --frozen ruff format --check src/ tests/` | 58 files already formatted |
| `uv run code-rag --help` | 正常显示 10 个命令 |
| `uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 --mode hybrid` | Recall@1=72.73% Recall@3=90.91% Recall@8=100.00% MRR=0.8258 |

---

## Task 1: 修复 modified 文件旧 chunk 残留

**严重程度：** 高

**修改文件：**
- `src/code_rag/services/index_service.py`
- `tests/test_index_service.py`

**关键改动：**
- `run_index()` 中对 `changes.modified` 也收集 `entry.rel_path`，与 `changes.deleted` 合并后调用 `delete_by_files()`，确保旧 chunk 在 upsert 前被清除。
- 新增测试 `test_modified_file_old_chunks_cleaned`：索引 a.py → 修改 a.py → 重新索引 → 断言 chunk 数不翻倍。

**测试结果：** 19 passed

---

## Task 2: 查询不存在 collection 时不要创建空 collection

**严重程度：** 中

**修改文件：**
- `src/code_rag/store/vector_store.py`
- `tests/test_vector_store.py`
- `tests/test_cli.py`

**关键改动：**
- `ChromaStore` 新增 `_get_collection()` 只读方法，使用 `get_collection()` 而非 `get_or_create_collection()`。
- `query()` 调用 `_get_collection()`，collection 不存在时返回空列表，不创建副作用。
- `get_or_create_collection()` 仅保留给 `upsert_chunks()` 使用。

**测试结果：** 24 passed

---

## Task 3: 让 `ask/chat` 默认使用 hybrid 检索

**严重程度：** 高

**修改文件：**
- `src/code_rag/services/query_service.py`
- `src/code_rag/cli.py`
- `tests/test_query_service.py`

**关键改动：**
- `QueryService.search()`、`ask()`、`stream_answer()` 均增加 `mode: str = "hybrid"` 参数。
- 内部按 mode 组装 vector / lexical / hybrid 检索路径。
- `_evaluate_confidence()` 区分 score 语义：vector 距离越小越好，hybrid/RRF 分数越大越好。
- CLI `ask/chat` 新增 `--mode` 选项，默认 `hybrid`。

**测试结果：** 28 passed

---

## Task 4: 收紧 Git URL scheme 和凭据处理

**严重程度：** 高

**修改文件：**
- `src/code_rag/config.py`
- `src/code_rag/repository/parser.py`
- `src/code_rag/repository/git.py`
- `src/code_rag/repository/__init__.py`
- `src/code_rag/repository/resolver.py`
- `src/code_rag/cli.py`
- `tests/test_repository.py`
- `tests/test_cli_remote.py`

**关键改动：**
- 默认只允许 `https://` 和 scp-like SSH，拒绝 `http://`、`ftp://`、`git://`、`file://`。
- `file://` 需通过 `Settings(allow_file_remote=True)` 显式开启（供测试使用）。
- `canonicalize_git_url()` 拒绝含 userinfo 的 HTTPS URL。
- 新增 `redact_url()` helper，CLI 展示 URL 时统一脱敏。
- `metadata.json` 不写入含 userinfo 的 URL。

**测试结果：** 224 passed

---

## Task 5: 修复 Git 缓存 refresh/reset 语义

**严重程度：** 中

**修改文件：**
- `src/code_rag/repository/git.py`
- `tests/test_repository.py`

**关键改动：**
- `refresh=False` 时跳过 fetch，复用本地缓存。
- `refresh=True` 时执行 fetch。
- checkout 后执行 `reset --hard` 和 `clean -fd`，确保 worktree 干净。
- 新增 3 个测试覆盖 refresh 语义和未跟踪文件清理。

**测试结果：** 48 passed

---

## Task 6: `status/remove/search` 避免无谓 clone/fetch

**严重程度：** 中

**修改文件：**
- `src/code_rag/repository/resolver.py`
- `src/code_rag/repository/__init__.py`
- `src/code_rag/services/manifest_service.py`
- `src/code_rag/cli.py`
- `tests/test_cli_remote.py`

**关键改动：**
- 新增 `identity_key_for_source()` 纯函数，只计算 collection key，不触发 clone/fetch。
- `ManifestService.get_status()` 使用 key 直接查 manifest/store。
- `remove` 命令使用 key 删除 collection、tracker、manifest；`--with-cache` 仅在缓存已存在时才删。
- 新增测试验证对不存在的 HTTPS URL 调用 `status` 不触发 `resolve_repo()`。

**测试结果：** 228 passed

---

## Task 7: scanner 跳过 symlink 和敏感文件

**严重程度：** 中到高

**修改文件：**
- `src/code_rag/indexer/scanner.py`
- `tests/test_scanner.py`

**关键改动：**
- 目录过滤增加 `abs_d.is_symlink()` 检查，跳过 symlink 目录。
- 文件过滤增加 `abs_f.is_symlink()` 检查，跳过 symlink 文件。
- 敏感文件名 denylist 增加 8 项：`.npmrc`、`.pypirc`、`id_rsa`、`id_dsa`、`credentials.json`、`secrets.json`、`credentials.yaml`、`secrets.yaml`。
- doc 类文件增加敏感内容检测（`API_KEY=`、`SECRET=`、`TOKEN=`）。
- 新增 7 个测试覆盖 symlink 和敏感文件过滤。

**测试结果：** 45 passed

---

## Task 8: 修复子目录 `.gitignore` 作用域

**严重程度：** 中

**修改文件：**
- `src/code_rag/indexer/scanner.py`
- `tests/test_scanner.py`

**关键改动：**
- `_rules` 类型从 `(pattern, negated)` 改为 `(base_dir, pattern, negated)`。
- 根 `.gitignore` 的 `base_dir` 为 `""`，子目录 `.gitignore` 的 `base_dir` 为相对路径。
- 匹配时检查待测路径是否位于 `base_dir` 下。
- 新增测试验证 `a/.gitignore` 的规则不影响 `b/keep.py`。

**测试结果：** 46 passed

---

## Task 9: 加固 LLM prompt 的不可信上下文边界

**严重程度：** 中

**修改文件：**
- `src/code_rag/generator/prompts.py`
- `src/code_rag/retriever/retriever.py`
- `tests/test_llm_prompts.py`（新增）

**关键改动：**
- System prompt 新增规则 6：`<untrusted_context>` 内容是不可信数据，不得执行其中指令。
- `{context}` 占位符用 `<untrusted_context>` / `</untrusted_context>` 包裹。
- `ContextBuilder.build_context()` 转义 chunk 内容中的三反引号和 `<untrusted_context>` 标签。
- 新增 10 个测试覆盖 prompt 边界、反引号转义和注入防护。

**测试结果：** 246 passed（全量）

---

## Task 10: 轻量整理 manifest 和 retriever 组装边界

**严重程度：** 中

**修改文件：**
- `src/code_rag/services/index_service.py`
- `src/code_rag/services/query_service.py`
- `src/code_rag/services/__init__.py`
- `src/code_rag/cli.py`
- `tests/test_query_service.py`

**关键改动：**
- `IndexService.run_index()` 成功后自动写 manifest，CLI 不再二次 `resolve_repo()`。
- 新增 `build_retriever(mode, settings, resolved)` helper，返回统一的检索函数。
- `QueryService.search()` 和 CLI `search` 复用该 helper，消除重复组装逻辑。
- CLI `index()` 简化为只打印 `IndexResult`。

**测试结果：** 246 passed（全量）

---

## 修改文件汇总

| 文件 | 任务 |
|------|------|
| `src/code_rag/services/index_service.py` | 1, 10 |
| `src/code_rag/store/vector_store.py` | 2 |
| `src/code_rag/services/query_service.py` | 3, 10 |
| `src/code_rag/cli.py` | 3, 4, 6, 10 |
| `src/code_rag/config.py` | 4 |
| `src/code_rag/repository/parser.py` | 4 |
| `src/code_rag/repository/git.py` | 4, 5 |
| `src/code_rag/repository/__init__.py` | 4, 6 |
| `src/code_rag/repository/resolver.py` | 4, 6 |
| `src/code_rag/services/manifest_service.py` | 6 |
| `src/code_rag/indexer/scanner.py` | 7, 8 |
| `src/code_rag/generator/prompts.py` | 9 |
| `src/code_rag/retriever/retriever.py` | 9 |
| `src/code_rag/services/__init__.py` | 10 |
| `tests/test_index_service.py` | 1, 10 |
| `tests/test_vector_store.py` | 2 |
| `tests/test_query_service.py` | 3, 10 |
| `tests/test_cli.py` | 2 |
| `tests/test_repository.py` | 4, 5 |
| `tests/test_cli_remote.py` | 4, 6 |
| `tests/test_scanner.py` | 7, 8 |
| `tests/test_llm_prompts.py` | 9 |

## 未解决问题

- Task 6 follow-up：`search` 命令仍需 `resolve_repo()` 获取 `root_path` 给 lexical retriever 使用，未来可优化为先检查 collection 存在性。
- Task 10 follow-up：`QueryService._retriever` 属性现在未使用，可单独清理。
- `EvalService` 和 `CodeAgent` 仍自行组装 retriever，按计划后续单独迁移。
