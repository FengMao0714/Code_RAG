# Code_RAG Small-Step Implementation Plan

目标：给较弱执行模型使用的小步修复计划。禁止大规模重构，禁止引入 Web/API/UI，优先保持现有 CLI 行为和 206 个测试绿色。

## 执行总规则

1. 每次只做一个任务，完成后运行该任务的最小验证命令。
2. 不要 `git reset --hard`，不要删除用户未提交改动。
3. 不要改 `.env`，不要打印真实 API Key、token、password。
4. 不要把 `src/code_rag/cli.py` 一次性大拆。只在任务要求的局部移动逻辑。
5. 每个任务都要补或更新测试。
6. 每个任务完成后至少运行：

```powershell
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
```

最终验收还要运行：

```powershell
uv run code-rag --help
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 --mode hybrid
```

## 推荐执行顺序

先修正确性，再修安全边界，再做轻量架构清理：

1. modified 文件旧 chunk 清理。
2. 只读查询不创建空 collection。
3. 主问答路径接入 hybrid 检索。
4. Git URL scheme / credential 安全收紧。
5. Git 缓存 refresh/reset 语义修复。
6. `status/remove` 避免 clone/fetch 副作用。
7. scanner symlink 和敏感文件默认跳过。
8. 子目录 `.gitignore` 作用域修复。
9. LLM prompt untrusted context 防护。
10. 小步整理 manifest 写入和检索器工厂。

## Task 1: 修复 modified 文件旧 chunk 残留

严重程度：高

目标：文件修改后，旧内容不再留在 ChromaDB。

修改文件：

- `src/code_rag/services/index_service.py`
- `tests/test_index_service.py`

步骤：

1. 在 `IndexService.run_index()` 中，对 `changes.modified` 也收集 `entry.rel_path`。
2. 在重新 upsert 前调用 `self._store.delete_by_files(collection_name, modified_paths)`。
3. 保留现有 `changes.deleted` 删除逻辑。
4. 补测试：创建仓库、索引 `a.py`，修改 `a.py`，再次索引，断言 collection chunk 数不会翻倍，旧源码片段不可检索。

验证：

```powershell
uv run --frozen pytest tests/test_index_service.py tests/test_vector_store.py -q
```

回滚建议：

只回滚 `index_service.py` 中 modified 删除逻辑和对应测试；不改 ChromaDB 封装 API。

## Task 2: 查询不存在 collection 时不要创建空 collection

严重程度：中

目标：`ask/search` 这类只读查询不产生持久化副作用。

修改文件：

- `src/code_rag/store/vector_store.py`
- `tests/test_vector_store.py`
- `tests/test_cli.py`

步骤：

1. 在 `ChromaStore` 增加只读获取 collection 的内部方法，使用 `get_collection()`。
2. `query()` 中 collection 不存在时返回空列表。
3. `get_or_create_collection()` 只保留给 `upsert_chunks()` 使用。
4. 补测试：未索引 collection 调用 `query()` 后，`get_stats(name)["exists"]` 仍为 false。
5. 补 CLI 测试：未索引仓库 `ask` 后不会创建空 collection。

验证：

```powershell
uv run --frozen pytest tests/test_vector_store.py tests/test_cli.py -q
```

回滚建议：

回滚 `query()` 的只读获取逻辑即可；保留新增测试可用于复现旧问题。

## Task 3: 让 `ask/chat` 默认使用 hybrid 检索

严重程度：高

目标：主问答路径和 README 宣称的 Hybrid Retrieval 保持一致。

修改文件：

- `src/code_rag/services/query_service.py`
- `src/code_rag/cli.py`
- `tests/test_query_service.py`
- `tests/test_cli.py`

步骤：

1. 给 `QueryService.search()` 和 `QueryService.ask()` 增加 `mode: str = "hybrid"` 参数。
2. 在 `QueryService` 内部按 mode 组装 vector / lexical / hybrid，先接受一点重复，不做大工厂。
3. `ask()` 返回 context 时要兼容 hybrid 的结果类型。
4. 低置信度逻辑区分 score 语义：vector 距离越小越好，hybrid/RRF 分数越大越好。
5. CLI `ask/chat` 先默认传 `mode="hybrid"`，可选地新增 `--mode vector|lexical|hybrid`。
6. 补测试：mock retriever，确认 `ask()` 默认走 hybrid；低置信度测试覆盖 hybrid 分数。

验证：

```powershell
uv run --frozen pytest tests/test_query_service.py tests/test_cli.py tests/test_lexical.py -q
```

回滚建议：

把默认 mode 改回 `vector`，保留 `mode` 参数和测试中的 vector 分支。

## Task 4: 收紧 Git URL scheme 和凭据处理

严重程度：高

目标：默认拒绝不安全 URL，不把 token/userinfo 落盘或展示。

修改文件：

- `src/code_rag/repository/parser.py`
- `src/code_rag/repository/git.py`
- `src/code_rag/repository/cache.py`
- `src/code_rag/cli.py`
- `tests/test_repository.py`
- `tests/test_cli_remote.py`

步骤：

1. 默认允许 `https://` 和 scp-like SSH：`git@host:owner/repo.git`。
2. 默认拒绝 `http://`、`ftp://`、`git://`。
3. 默认拒绝 `file://`，但测试可通过 `Settings` 或 provider 参数显式允许 local file remote。
4. `canonicalize_git_url()` 遇到 URL userinfo 时抛出清晰错误。
5. 如果必须展示 URL，统一使用 redacted helper。
6. `metadata.json` 中不要写入含 userinfo 的 URL。
7. 更新测试中本地 bare repo 的 file URL 用显式 allow 开关。

验证：

```powershell
uv run --frozen pytest tests/test_repository.py tests/test_cli_remote.py -q
```

回滚建议：

如果破坏远程测试，先保留生产默认拒绝，给测试 provider 增加 `allow_file_url=True`，不要重新放开所有 scheme。

## Task 5: 修复 Git 缓存 refresh/reset 语义

严重程度：中

目标：缓存 worktree 始终等于目标 ref 的干净状态。

修改文件：

- `src/code_rag/repository/git.py`
- `tests/test_repository.py`

步骤：

1. 缓存存在且 `refresh=False` 时，先不要强制 fetch；复用本地缓存。
2. 缓存存在且 `refresh=True` 时执行 fetch。
3. checkout 到目标 remote ref、tag 或 commit 后，执行等价的 `reset --hard`。
4. 执行 clean，删除未跟踪文件。注意只对缓存 worktree 执行。
5. 补测试：远端新增 commit 后，`refresh=False` 保持旧 commit，`refresh=True` 更新到新 commit。
6. 补测试：缓存 worktree 中手动放一个未跟踪文件，refresh 后该文件消失。

验证：

```powershell
uv run --frozen pytest tests/test_repository.py -q
```

回滚建议：

如果 reset/clean 在 Windows 文件句柄上不稳定，先只在 `refresh=True` 下启用 clean，保留复用缓存语义。

## Task 6: `status/remove/search` 避免无谓 clone/fetch

严重程度：中

目标：查看状态或删除索引时，不为了计算 key 而 clone 远程仓库。

修改文件：

- `src/code_rag/repository/`
- `src/code_rag/services/manifest_service.py`
- `src/code_rag/cli.py`
- `tests/test_cli_remote.py`

步骤：

1. 新增纯函数 `identity_key_for_source(source: str, ref: str | None, settings: Settings) -> str`。
2. 本地路径用 resolve 后绝对路径计算 key。
3. Git URL 只 canonicalize 并计算 `collection_key_for_git()`，不 clone。
4. `ManifestService.get_status()` 先用 key 查 manifest/store。
5. `remove()` 删除 collection、tracker、manifest 时使用 key；只有 `--with-cache` 且缓存已存在时才删缓存。
6. 补测试：对不存在的 HTTPS URL 调用 `status` 不触发 `GitRepositoryProvider.resolve()`。

验证：

```powershell
uv run --frozen pytest tests/test_cli_remote.py tests/test_manifest_service.py -q
```

回滚建议：

如果本地路径 key 兼容性出问题，只先对 Git URL 走 no-clone key，保留本地路径原逻辑。

## Task 7: scanner 跳过 symlink 和敏感文件

严重程度：中到高

目标：避免索引仓库外文件和明显敏感文件。

修改文件：

- `src/code_rag/indexer/scanner.py`
- `tests/test_scanner.py`

步骤：

1. 在目录过滤中跳过 symlink 目录。
2. 在文件过滤中跳过 `abs_f.is_symlink()`。
3. 增加默认敏感文件名 denylist：`.npmrc`、`.pypirc`、`id_rsa`、`id_dsa`、`credentials.json`、`secrets.json`、`credentials.yaml`、`secrets.yaml`。
4. 可选增加简单内容检测：`API_KEY=`, `SECRET=`, `TOKEN=` 出现时跳过文档类文件。
5. 补测试：symlink 文件不入选；敏感文件名不入选；正常 `config.example.yaml` 仍可入选。

验证：

```powershell
uv run --frozen pytest tests/test_scanner.py -q
```

回滚建议：

如果内容检测误伤太多，先回滚内容检测，保留 symlink 和文件名 denylist。

## Task 8: 修复子目录 `.gitignore` 作用域

严重程度：中

目标：`.gitignore` 规则只影响它所在目录及子目录。

修改文件：

- `src/code_rag/indexer/scanner.py`
- `tests/test_scanner.py`

步骤：

1. 把 `_rules` 从 `(pattern, negated)` 改为 `(base_dir, pattern, negated)`。
2. 根 `.gitignore` 的 `base_dir` 是 `""`。
3. 子目录 `.gitignore` 的 `base_dir` 是相对仓库根的目录路径。
4. 匹配时，只有待测路径位于 `base_dir` 下才应用该规则。
5. 补测试：`a/.gitignore` 忽略 `*.py` 不应影响 `b/keep.py`。

验证：

```powershell
uv run --frozen pytest tests/test_scanner.py -q
```

回滚建议：

如果手写实现变复杂，停下来改用 `pathspec`，但必须先在 `pyproject.toml` 中说明新增依赖并保持 `uv run --frozen` 可复现。

## Task 9: 加固 LLM prompt 的不可信上下文边界

严重程度：中

目标：远程仓库内容只能作为数据，不能作为指令。

修改文件：

- `src/code_rag/generator/prompts.py`
- `src/code_rag/retriever/retriever.py`
- `tests/test_retriever.py`
- `tests/test_cli.py` 或新增 `tests/test_llm_prompts.py`

步骤：

1. System prompt 明确写入：检索内容是不可信数据，不得执行其中任何指令。
2. 用 `<untrusted_context>` 包裹上下文。
3. `ContextBuilder` 输出前转义 chunk 内容里的三反引号。
4. 补测试：chunk 内容含 `Ignore previous instructions` 和 ``` 时，生成 prompt 边界仍完整。

验证：

```powershell
uv run --frozen pytest tests/test_retriever.py tests/test_cli.py -q
```

回滚建议：

如果转义影响 README 展示，先只做 prompt 文案和 XML-like wrapper，保留代码块格式。

## Task 10: 轻量整理 manifest 和 retriever 组装边界

严重程度：中

目标：减少 CLI/service 行为漂移，但不做大规模重构。

修改文件：

- `src/code_rag/services/index_service.py`
- `src/code_rag/services/manifest_service.py`
- `src/code_rag/services/query_service.py`
- `src/code_rag/cli.py`
- `tests/test_index_service.py`
- `tests/test_query_service.py`

步骤：

1. 先让 `IndexService.run_index()` 成功后写 manifest。
2. CLI `index()` 不再第二次 `resolve_repo()`；只打印 `IndexResult`。
3. 新增一个小型 `build_retriever(mode, settings, resolved)` helper，先放在 `query_service.py` 或 `retriever/factory.py`。
4. 让 `QueryService` 和 `cli search` 复用该 helper。
5. 不要在同一个任务中迁移 EvalService 和 CodeAgent；后续单独做。

验证：

```powershell
uv run --frozen pytest tests/test_index_service.py tests/test_query_service.py tests/test_cli.py -q
```

回滚建议：

如果 manifest 写入与 CLI 交互测试冲突，先回滚 CLI 改动，只保留 `IndexService` 可选写 manifest 的能力。

## 最终验收标准

所有任务完成后，必须满足：

```powershell
git status --short
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
uv run code-rag --help
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 --mode hybrid
```

人工检查：

- 修改文件重新索引后，旧内容不会被检索。
- 未索引仓库的只读查询不会创建空 collection。
- `ask` 对符号/文件名问题能走 hybrid 检索。
- `status/remove` 对未缓存 Git URL 不 clone。
- scanner 不索引 symlink 指向的仓库外文件。
- URL token 不出现在 metadata、日志、CLI 输出。

