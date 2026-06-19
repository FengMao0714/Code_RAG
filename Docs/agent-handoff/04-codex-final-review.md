# Codex Final Review

审查时间：2026-06-12  
审查对象：Claude Code 按 `docs/agent-handoff/02-implementation-plan.md` 产生的当前未提交改动  
审查方式：本地只读代码审查 + 验收命令复跑 + 临时目录边界复现

说明：已尝试调用用户指定的 `Code Reviewer` 与 `Application Security Engineer` 子代理，但两个子代理均因额度限制报错，未返回可用审查结果。因此本报告由主 Codex 基于同一检查清单补齐。

## 总体结论

结论：不建议直接合并。整体改动范围基本围绕 `02-implementation-plan.md`，全量测试、lint、format、CLI help、eval 均通过；但仍有 3 个需要先修的小范围问题，其中 2 个属于计划明确要求但未完整达成，1 个可能导致 `.gitignore` 规则漏过滤敏感文件。

不建议整批回滚。建议让执行模型只做小补丁：修 URL scheme 显式拒绝、修 `search` 的 no-clone 预检查、修子目录 `.gitignore` 锚定规则，并补对应回归测试。

## 本地验收结果

已复跑并通过：

```powershell
uv run --frozen pytest -q
# 246 passed in 44.50s

uv run --frozen ruff check src/ tests/
# All checks passed!

uv run --frozen ruff format --check src/ tests/
# 58 files already formatted

uv run code-rag --help
# 正常显示 10 个命令

uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 --mode hybrid
# Recall@1=72.73% Recall@3=90.91% Recall@8=100.00% MRR=0.8258
```

额外边界复现：

```text
parse_repo_source("http://...") -> local
parse_repo_source("ftp://...") -> local
parse_repo_source("git://...") -> local
parse_repo_source("file://...") -> local

a/.gitignore contains "/ignore.py"
RepoScanner output still includes "a/ignore.py"
```

## Findings

### F1 高：不安全 URL scheme 没有被显式拒绝，而是落入本地路径分支

影响范围：
- `src/code_rag/repository/parser.py:36`
- `src/code_rag/repository/parser.py:55`
- `src/code_rag/repository/parser.py:59`
- `src/code_rag/repository/parser.py:83`
- `src/code_rag/repository/parser.py:90`
- `tests/test_repository.py:71`
- `tests/test_repository.py:76`
- `tests/test_repository.py:81`
- `tests/test_repository.py:86`

问题：
`02-implementation-plan.md` Task 4 要求默认拒绝 `http://`、`ftp://`、`git://`、`file://`。当前实现只让 `_looks_like_git_url()` 对这些 scheme 返回 `False`，随后 `parse_repo_source()` 在第 59-60 行把它们当作 local source 返回。测试也把“拒绝”定义成 `SOURCE_TYPE_LOCAL`，这与计划目标不一致。

影响：
- 用户输入不安全 URL 时不会得到明确的安全错误，只会在后续流程表现为本地路径不存在。
- `status/remove` 等依赖 `identity_key_for_source()` 的路径可能为这些 URL-like 字符串生成 local collection key，行为混淆。
- 安全策略不可验证，测试目前会保护错误行为。

建议修复：
1. 在 `parse_repo_source()` 中，当 `urlparse(text).scheme` 非空且不是允许的安全 scheme 时，直接抛 `InvalidRepoSourceError`。
2. `file://` 仅在 `allow_file=True` 时返回 git source，否则也抛出同类错误。
3. 修改 `tests/test_repository.py` 中相关测试：`http/ftp/git/file` 默认应 `pytest.raises(InvalidRepoSourceError)`，`file://` 仅 `allow_file=True` 通过。

验收标准：
- `uv run --frozen pytest tests/test_repository.py tests/test_cli_remote.py -q`
- 临时验证 `parse_repo_source("http://example.com/repo.git")` 抛出 `InvalidRepoSourceError`。

回滚建议：
不回滚 Task 4 的其他凭据脱敏改动，只针对 parser 和测试做小补丁。

### F2 中：`search` 仍在检查 collection 前调用 `resolve_repo()`，未完整满足 Task 6

影响范围：
- `src/code_rag/cli.py:403`
- `src/code_rag/cli.py:422`
- `src/code_rag/cli.py:424`
- `src/code_rag/cli.py:429`
- `src/code_rag/cli.py:431`
- `docs/agent-handoff/02-implementation-plan.md:120`
- `docs/agent-handoff/02-implementation-plan.md:131`

问题：
Task 6 标题要求 `status/remove/search` 避免无谓 clone/fetch。当前 `status/remove` 已改为 key-first，但 `search` 在第 424 行先 `resolve_repo(source, ...)`，然后才查 collection 是否存在。这意味着对未索引远程 URL 做 `code-rag search` 仍可能 clone/fetch，违背“只读调试检索不产生远程副作用”的目标。

影响：
- 未索引远程仓库执行 `search` 可能产生网络访问和缓存目录。
- 安全上会扩大用户只想检查索引状态时的外部访问面。
- 执行日志已把这个点列为 follow-up，但从计划合规角度应视为未完成。

建议修复：
1. 在 `search` 中先用 `identity_key_for_source(source, ref, settings)` 计算 collection key。
2. 用该 key 查 `ChromaStore.get_stats()`；不存在则直接提示“尚未索引”，不要调用 `resolve_repo()`。
3. 只有 collection 存在后，再按现有逻辑 `resolve_repo()` 并构建 retriever。
4. 增加 CLI 测试：对不存在的 HTTPS URL 调用 `search` 时 mock/spy `resolve_repo()`，断言未调用。

验收标准：
- `uv run --frozen pytest tests/test_cli_remote.py tests/test_cli.py -q`
- 对未缓存 HTTPS URL 执行 `code-rag search <url> x` 不创建 cache 目录、不触发 provider resolve。

回滚建议：
不需要回滚 `build_retriever()` helper；只调整 `search` 的 stats 预检查顺序。

### F3 中：子目录 `.gitignore` 的锚定规则仍按仓库根匹配，可能漏过滤

影响范围：
- `src/code_rag/indexer/scanner.py:247`
- `src/code_rag/indexer/scanner.py:258`
- `src/code_rag/indexer/scanner.py:260`
- `src/code_rag/indexer/scanner.py:264`
- `src/code_rag/indexer/scanner.py:278`
- `src/code_rag/indexer/scanner.py:286`
- `src/code_rag/indexer/scanner.py:472`
- `src/code_rag/indexer/scanner.py:476`

问题：
Task 8 要求子目录 `.gitignore` 的规则只作用于该目录及其子目录。当前实现保存了 `base_dir`，也检查了路径是否在作用域内，但匹配时仍把完整 repo-relative `rel_path` 传给 `_match_gitignore_pattern()`。因此 `a/.gitignore` 内的 `/ignore.py` 会被当成从仓库根匹配 `ignore.py`，无法匹配 `a/ignore.py`。

影响：
- 子目录 `.gitignore` 中带前导 `/` 的规则会漏过滤。
- 远程仓库中被子目录规则忽略的文件仍可能进入索引。
- 如果被漏掉的是局部 secrets/config 文件，存在敏感信息入库风险。

建议修复：
1. 在 `GitignoreFilter.is_ignored()` 中，当 `base_dir` 命中后，计算 `scoped_rel_path`：若 `rel_path == base_dir` 则为 `""`，否则去掉 `base_dir + "/"` 前缀。
2. 调用 `_match_gitignore_pattern(pattern, scoped_rel_path, is_dir)`。
3. 补测试：`a/.gitignore` 写 `/ignore.py`，断言 `a/ignore.py` 被忽略且 `b/ignore.py` 不受影响。

验收标准：
- `uv run --frozen pytest tests/test_scanner.py -q`
- 临时仓库 `a/.gitignore` 含 `/ignore.py` 时，`RepoScanner` 不返回 `a/ignore.py`。

回滚建议：
不回滚 Task 8 的整体结构；只修 scoped path 传参。

### F4 低：敏感内容检测覆盖面有限，作为残余安全风险记录

影响范围：
- `src/code_rag/indexer/scanner.py:191`
- `src/code_rag/indexer/scanner.py:192`
- `src/code_rag/indexer/scanner.py:193`
- `src/code_rag/indexer/scanner.py:194`
- `src/code_rag/indexer/scanner.py:195`
- `tests/test_scanner.py:459`
- `tests/test_scanner.py:479`

问题：
Task 7 将内容检测标为“可选简单检测”，当前实现只检测文档类文件中的 `API_KEY=`、`SECRET=`、`TOKEN=`，且是大小写敏感、格式固定。代码文件即使包含敏感模式也明确不跳过。

影响：
这是计划允许的轻量实现，不是阻塞项。但安全上仍可能漏掉 `api_key =`、`token:`、`password=`、私钥块等常见格式。

建议修复：
本轮不要求扩大范围，避免误伤。后续如果要增强，单独建一个小任务：仅对文档类文件增加大小写不敏感的少量模式，并补误伤测试。

回滚建议：
无需回滚。

### F5 低：`git clean -fd` 满足未跟踪文件清理，但没有覆盖 ignored untracked 文件

影响范围：
- `src/code_rag/repository/git.py:301`
- `src/code_rag/repository/git.py:303`
- `src/code_rag/repository/git.py:307`
- `docs/agent-handoff/02-implementation-plan.md:111`
- `docs/agent-handoff/02-implementation-plan.md:112`

问题：
Task 5 要求 refresh/checkout 后清理未跟踪文件。当前使用 `git clean -fd`，可以删除普通 untracked 文件，但不会删除 ignored untracked 文件。由于 scanner 本身会读取 `.gitignore`，这不一定造成功能 bug；但缓存 worktree 的“完全干净”语义仍有残余边界。

建议修复：
本轮可不阻塞。若后续希望缓存 worktree 与目标 ref 更严格一致，改成 `git clean -fdx` 并补测试：ignored untracked 文件在 refresh 后消失。注意只允许在 repo cache worktree 内执行。

回滚建议：
无需回滚。

## 计划符合度

| 任务 | 结论 | 说明 |
|---|---|---|
| Task 1 modified 旧 chunk 清理 | 通过 | 相关测试与全量测试通过。 |
| Task 2 查询不存在 collection 不创建空 collection | 通过 | 全量测试通过，符合计划。 |
| Task 3 `ask/chat` 默认 hybrid | 通过 | CLI 与 service 均已支持 mode。 |
| Task 4 Git URL scheme 和凭据处理 | 部分通过 | 凭据脱敏方向正确，但不安全 scheme 被当成 local，见 F1。 |
| Task 5 Git cache refresh/reset | 基本通过 | refresh/fetch/reset/clean 已覆盖；ignored untracked 是低风险残余。 |
| Task 6 `status/remove/search` 避免 clone/fetch | 部分通过 | `status/remove` 通过，`search` 未完成，见 F2。 |
| Task 7 scanner 跳过 symlink 和敏感文件 | 基本通过 | 计划内简单检测已做；覆盖面有限，见 F4。 |
| Task 8 子目录 `.gitignore` 作用域 | 部分通过 | 作用域检查有了，但锚定规则 scoped path 错，见 F3。 |
| Task 9 LLM prompt 不可信上下文 | 通过 | 新增 prompt 边界和测试，未发现阻塞问题。 |
| Task 10 manifest/retriever 边界整理 | 基本通过 | 未见大规模无关重构；执行日志提到 `EvalService`/`CodeAgent` 未迁移，符合计划“不在同一任务迁移”。 |

## 无关改动检查

当前源码改动集中在计划列出的模块：CLI、config、repository、scanner、retriever/query/index/vector store、测试。新增 `tests/test_llm_prompts.py` 与 Task 9 匹配，新增 `docs/agent-handoff/03-claude-execution-log.md` 属于执行记录。未发现明显 UI、部署、依赖、格式化全仓漂移或与计划无关的大规模重构。

需要注意：`src/code_rag/config.py`、`src/code_rag/services/__init__.py`、`src/code_rag/repository/__init__.py` 虽不都在每个子任务的文件列表里，但它们用于暴露配置项和 helper，属于当前计划可解释范围。

## 测试充分性

已足够覆盖主路径和大部分计划项，但以下回归测试仍缺：

1. URL parser 对 `http://`、`ftp://`、`git://`、默认 `file://` 抛 `InvalidRepoSourceError`。
2. CLI `search` 对未索引 HTTPS URL 不调用 `resolve_repo()`。
3. 子目录 `.gitignore` 的 `/anchored` 规则按子目录根匹配。
4. 可选：`git clean -fdx` 若采纳，需要 ignored untracked 文件清理测试。

## 给执行模型的最小修复任务

### Task A：显式拒绝不安全 URL scheme

修改文件：
- `src/code_rag/repository/parser.py`
- `tests/test_repository.py`

步骤：
1. 在 `parse_repo_source()` 中解析 `scheme`。
2. 如果存在 `scheme` 且不在默认安全 scheme 中，直接抛 `InvalidRepoSourceError`；`file` 仅 `allow_file=True` 时允许。
3. 把当前断言 `SOURCE_TYPE_LOCAL` 的 URL 拒绝测试改为 `pytest.raises()`。

验收：
```powershell
uv run --frozen pytest tests/test_repository.py tests/test_cli_remote.py -q
```

### Task B：让 `search` 先查 collection，再 resolve repo

修改文件：
- `src/code_rag/cli.py`
- `tests/test_cli_remote.py` 或 `tests/test_cli.py`

步骤：
1. 在 `search()` 中先调用 `identity_key_for_source()` 计算 key。
2. 先用 key 查 stats；不存在则直接返回提示。
3. 仅当 stats 存在后调用 `resolve_repo()` 和 `build_retriever()`。
4. 增加测试断言未索引远程 URL 的 `search` 不调用 `resolve_repo()`。

验收：
```powershell
uv run --frozen pytest tests/test_cli_remote.py tests/test_cli.py -q
```

### Task C：修复子目录 `.gitignore` 锚定规则

修改文件：
- `src/code_rag/indexer/scanner.py`
- `tests/test_scanner.py`

步骤：
1. `GitignoreFilter.is_ignored()` 命中 `base_dir` 后，计算相对于该 `base_dir` 的路径。
2. 把 scoped path 传给 `_match_gitignore_pattern()`。
3. 增加测试：`a/.gitignore` 写 `/ignore.py`，只忽略 `a/ignore.py`，不影响 `b/ignore.py`。

验收：
```powershell
uv run --frozen pytest tests/test_scanner.py -q
```

## 最终合并前验收标准

完成 Task A-C 后，再跑：

```powershell
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
uv run code-rag --help
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8 --mode hybrid
```

人工验收：
- 不安全 URL scheme 默认抛清晰错误，不落入 local。
- 未索引远程 URL 的 `search/status/remove` 不触发 clone/fetch。
- 子目录 `.gitignore` 的普通规则和 `/anchored` 规则都只影响该子目录树。
- URL token 不出现在 metadata、日志、CLI 输出。

## 回滚建议

不建议整体回滚 Claude Code 的改动，因为主路径验收通过且大多数计划项已落地。

仅在时间很紧、无法补修时，建议暂缓合并以下部分完成状态：
- Task 4 不标记完成，直到 F1 修复。
- Task 6 不标记完成，直到 F2 修复。
- Task 8 不标记完成，直到 F3 修复。

如果某个小补丁导致测试不稳定，优先回滚该小补丁，不要回滚整个远程仓库支持改动。
