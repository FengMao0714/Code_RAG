# Code_RAG Refactor Plan

## 一句话目标

把当前的本地代码库 RAG CLI，重构成一个更适合 Agent 方向简历展示的代码库智能助手：

- 支持本地路径和 GitHub/Git 仓库链接输入。
- 自动拉取远程仓库到本地缓存，再复用现有扫描、切分、索引、检索链路。
- 用稳定的仓库身份标识管理索引、manifest、缓存和删除逻辑。
- 保留现有 `index -> search/ask/chat -> status/list/remove -> eval` 闭环。
- 在此基础上增加轻量 Code Agent 能力：任务拆解、相关文件定位、修改建议、测试建议，先不自动改用户仓库。

## 当前判断

现有项目已经有不错的 RAG 底座：

- `src/code_rag/indexer/`：文件扫描、tree-sitter 解析、语义切分、embedding。
- `src/code_rag/retriever/`：向量检索、词法检索、hybrid、rerank。
- `src/code_rag/services/`：索引、查询、评估、manifest 服务雏形。
- `src/code_rag/evaluation/`：golden query、Recall@k、MRR、报告。
- `src/code_rag/cli.py`：Typer CLI 命令入口。

主要短板：

- CLI 参数目前是 `repo_path: Path`，直接给 GitHub URL 会被当成本地路径。
- `IndexService.run_index()` 会执行 `Path(repo_path).resolve()`、`exists()`、`is_dir()`，因此当前只能索引本地目录。
- Chroma collection 名称基于本地绝对路径生成，远程仓库缓存路径变化后可能造成索引身份不稳定。
- manifest/list/status/remove 目前围绕本地路径组织，还没有 `source_type/url/ref/commit/cache_path` 等信息。
- 项目现在更像 Code RAG，还差一步包装成 Code Agent。

## 重构原则

1. 先扩展输入源抽象，再改服务层，不要直接在 CLI 里堆 `if url then clone`。
2. 现有本地路径用法必须保持兼容：

```bash
uv run code-rag index .
uv run code-rag ask . "CLI 入口在哪里？"
```

3. 所有测试默认离线，不能依赖真实 GitHub、真实 LLM API、真实 embedding 模型下载。
4. GitHub URL 支持先做公开仓库，私有仓库和 token 认证放到后续增强。
5. 不做 Web UI。当前阶段优先把 CLI、服务层、测试和文档打磨扎实。
6. 不提交 `.env`、ChromaDB 数据、模型缓存、临时 clone 仓库和 debug 文件。

## 目标命令

第一阶段完成后，至少支持这些命令：

```bash
# 本地仓库，保持兼容
uv run code-rag index E:\code\Code_RAG
uv run code-rag ask E:\code\Code_RAG "索引流程在哪里实现？"

# 远程仓库，新增能力
uv run code-rag index https://github.com/owner/repo
uv run code-rag index https://github.com/owner/repo --ref main
uv run code-rag index https://github.com/owner/repo --ref v1.0.0 --refresh
uv run code-rag ask https://github.com/owner/repo "项目入口在哪里？"
uv run code-rag search https://github.com/owner/repo "认证逻辑" --mode hybrid --explain

# 状态与清理
uv run code-rag list
uv run code-rag status https://github.com/owner/repo
uv run code-rag remove https://github.com/owner/repo --yes
uv run code-rag cache list
uv run code-rag cache prune --yes
```

第二阶段可以增加轻量 Agent 命令：

```bash
uv run code-rag agent https://github.com/owner/repo "解释登录流程并指出关键文件"
uv run code-rag agent . "如果要增加 GitHub URL 输入，应该改哪些模块？" --plan-only
```

## 目标架构

建议新增这些模块：

```text
src/code_rag/repository/
  __init__.py
  models.py          # RepoSource, ResolvedRepo, RepoIdentity
  parser.py          # 判断输入是本地路径还是 Git URL
  resolver.py        # 统一 resolve(source) -> ResolvedRepo
  local.py           # LocalRepositoryProvider
  git.py             # GitRepositoryProvider
  cache.py           # 远程仓库缓存目录管理

src/code_rag/agent/
  __init__.py
  models.py          # AgentTask, AgentStep, AgentReport
  planner.py         # 根据问题生成检索计划
  code_agent.py      # 检索、证据整合、回答/修改建议
```

关键数据模型：

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class RepoSource:
    raw: str
    kind: str              # "local" | "git"
    ref: str | None = None

@dataclass(frozen=True)
class RepoIdentity:
    source_type: str       # "local" | "git"
    display_name: str
    canonical_source: str  # local absolute path or canonical git url
    ref: str | None
    commit: str | None
    collection_key: str    # stable key used by Chroma and manifest

@dataclass(frozen=True)
class ResolvedRepo:
    source: RepoSource
    identity: RepoIdentity
    root_path: Path        # actual local directory scanned by RepoScanner
    cache_path: Path | None
```

## Phase 0: 建立基线

目标：确认当前代码真实状态，避免 Claude Code 在脏工作区里误删用户改动。

执行：

```bash
git status --short
uv sync
uv run --frozen pytest tests/ -v
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
```

要求：

- 记录当前失败项，不要为了通过测试随手删除测试。
- 如果已有未提交改动，继续在现有基础上工作，不要 `git reset --hard`。
- 先阅读 `README.md`、`PROJECT_STATUS.md`、`CLAUDE.md`、`src/code_rag/cli.py`、`src/code_rag/services/*`。

## Phase 1: 仓库输入源抽象

目标：所有命令不再直接依赖 `Path` 作为业务输入，而是先解析成 `ResolvedRepo`。

实现：

1. 新增 `repository/models.py`。
2. 新增 `repository/parser.py`：
   - 识别本地路径：`.`、`E:\code\xxx`、`/path/to/repo`。
   - 识别 Git URL：`https://github.com/owner/repo`、`https://github.com/owner/repo.git`、`git@github.com:owner/repo.git`。
   - 暂不支持任意网页 URL。
3. 新增 `repository/resolver.py`：
   - `resolve_repo(source: str, ref: str | None, refresh: bool) -> ResolvedRepo`
   - 本地路径返回真实目录。
   - 远程 URL 交给 Git provider clone/fetch。
4. 在 `Settings` 中增加：
   - `repo_cache_dir`
   - `git_clone_depth`
   - `allow_private_git`

测试：

- 本地相对路径解析。
- Windows 绝对路径解析。
- GitHub HTTPS URL 规范化。
- `.git` 后缀规范化。
- SSH URL 规范化。
- 非法 URL 给出清晰错误。

## Phase 2: 远程 Git 缓存与拉取

目标：给 GitHub/Git URL 后可以自动 clone 到本地缓存目录。

实现建议：

- 优先使用项目已有依赖 `gitpython`。
- 缓存目录示例：

```text
{repo_cache_dir}/
  github.com_owner_repo/
    worktree/
    metadata.json
```

- 首次索引：
  - `git clone --depth 1 --branch <ref>` 或 GitPython 等价实现。
  - 记录 `canonical_url/ref/commit/cache_path/cloned_at/updated_at`。
- 后续索引：
  - 默认复用缓存。
  - `--refresh` 时执行 fetch/pull 或重新 clone。
- 如果 ref 是 tag/commit，要能 checkout 到对应版本。
- clone 失败时输出明确原因：网络失败、仓库不存在、认证失败、ref 不存在。

安全要求：

- 不在日志中打印 token。
- 默认不支持私有仓库 token。
- scanner 不能跟随逃出仓库根目录的 symlink。
- 继续忽略 `.git`、`node_modules`、`.env*`、build artifacts。

测试：

- 用测试里创建的本地 bare repo 或普通 repo 模拟 remote，不访问 GitHub。
- 测试首次 clone。
- 测试重复 resolve 复用缓存。
- 测试 `refresh=True` 后更新 commit。
- 测试 ref 不存在的错误信息。

## Phase 3: 服务层迁移

目标：`IndexService`、`QueryService`、`EvalService`、`ManifestService` 都基于 `ResolvedRepo` 工作。

改造点：

1. `IndexService.run_index()`：
   - 入参可以仍接受 `str | Path`，但内部先 `resolve_repo()`。
   - `RepoScanner` 扫描 `resolved.root_path`。
   - tracker 使用 `resolved.identity.collection_key`，不要只用本地绝对路径。
2. `QueryService.ask()`：
   - 同样先 resolve。
   - 检索时使用稳定 `collection_key`。
3. `ChromaStore.get_collection_name()`：
   - 新增 `get_collection_name_from_key(collection_key: str)`。
   - 保留旧方法兼容本地路径。
4. `ManifestService`：
   - manifest 记录 `source_type`、`canonical_source`、`display_name`、`ref`、`commit`、`cache_path`、`collection_name`、`last_indexed_at`。
5. `list/status/remove`：
   - 支持本地路径和 URL。
   - `remove` 默认只删除索引和 manifest，不删除 clone 缓存。
   - 删除缓存交给 `cache prune`。

测试：

- 本地路径索引行为不变。
- URL 输入最终扫描缓存目录。
- 同一 URL 同一 ref 多次索引命中同一 collection。
- 同一 URL 不同 ref 生成不同 collection key，或在 manifest 中明确策略。
- `remove URL --yes` 删除对应索引。

## Phase 4: CLI 体验重构

目标：CLI 参数从 `repo_path` 语义升级为 `source`，但不破坏老用户。

建议：

```python
source: str = typer.Argument(..., help="本地仓库路径或 Git 仓库 URL")
ref: str | None = typer.Option(None, "--ref", help="Git branch/tag/commit")
refresh: bool = typer.Option(False, "--refresh", help="强制刷新远程仓库缓存")
```

命令调整：

- `index source --ref --refresh`
- `ask source question --ref`
- `chat source --ref`
- `search source question --ref --mode --explain`
- `status source --ref`
- `remove source --ref --yes`
- `cache list`
- `cache prune --yes`

输出要求：

- 远程仓库输出显示：
  - source type
  - canonical URL
  - ref
  - resolved commit
  - cache path
  - indexed file count/chunk count
- 错误信息面向用户，不暴露 Python traceback，除非 `--verbose`。

## Phase 5: 轻量 Code Agent 能力

目标：让项目从 Code RAG 升级为 Code Agent，但先做只读分析和计划，不自动改文件。

新增 `agent` 命令：

```bash
uv run code-rag agent <source> "<task>" --plan-only
```

Agent 流程：

1. Planner：把用户任务拆成 3 到 6 个检索子问题。
2. Retriever：对每个子问题执行 hybrid search。
3. Evidence Builder：合并相关 chunk，按文件聚合证据。
4. Reasoner：生成：
   - 任务理解
   - 关键文件列表
   - 修改方案
   - 风险点
   - 建议运行的测试命令
5. Reviewer：检查回答是否引用了检索证据；证据不足时明确说不确定。

暂不做：

- 自动写 patch。
- 自动提交代码。
- 自动 push 或开 PR。

简历亮点写法：

> 基于 AST 语义切分、混合检索和工具化任务拆解构建代码库 Agent，支持本地路径与 GitHub 仓库链接输入，自动拉取仓库、建立索引、定位关键文件，并生成修改计划与测试建议。

## Phase 6: 评估与演示

目标：证明它不是只能跑 demo，而是能被评估。

保留现有 retrieval eval，并扩展：

- golden query 增加 Git URL source 场景，测试仍用本地临时 git repo。
- 指标继续保留：
  - Recall@1
  - Recall@3
  - Recall@8
  - MRR
  - expected file hit
  - expected symbol hit
- 新增 agent 评估可以先做结构化检查：
  - 是否列出关键文件
  - 是否包含测试建议
  - 是否在证据不足时拒绝臆测

演示脚本建议：

```bash
uv run code-rag index .
uv run code-rag search . "GitHub URL 应该在哪里接入？" --mode hybrid --explain
uv run code-rag agent . "支持 GitHub URL 输入需要改哪些模块？" --plan-only
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8
```

远程仓库演示：

```bash
uv run code-rag index https://github.com/owner/repo --ref main
uv run code-rag ask https://github.com/owner/repo "项目的 CLI 入口在哪里？"
uv run code-rag status https://github.com/owner/repo
```

## Phase 7: 文档更新

需要同步更新：

- `README.md`
  - 本地路径用法。
  - GitHub URL 用法。
  - 缓存目录说明。
  - `agent` 命令演示。
  - retrieval eval 结果展示。
- `PROJECT_STATUS.md`
  - 当前能力矩阵。
  - 已完成和未完成项。
- `.gitignore`
  - 加入 repo cache、reports、Chroma 本地数据、临时 clone 目录。

README 中推荐的项目介绍：

> Code_RAG 是一个面向代码库理解的 Agent/RAG CLI 工具，支持本地路径与 GitHub 仓库链接输入，能够自动拉取仓库、基于 tree-sitter 进行 AST 语义切分，结合向量检索、词法检索和 RRF 融合定位关键代码，并通过评估集计算 Recall@k 与 MRR。

## 验收标准

必须全部满足：

```bash
uv run --frozen pytest tests/ -v
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
uv run code-rag --help
uv run code-rag index .
uv run code-rag search . "CLI 入口在哪里？" --mode hybrid --explain
uv run code-rag eval . --dataset evals/code_rag_golden.yaml --top-k 8
```

远程能力验收：

- 给 `https://github.com/owner/repo` 能 clone 到缓存目录。
- 再次 index 默认复用缓存。
- `--refresh` 能刷新远程代码。
- `status` 能展示 URL、ref、commit、cache path。
- `remove` 能删除对应索引。
- `cache prune` 能清理缓存。
- 所有远程相关测试都不能依赖真实外网。

Agent 能力验收：

- `agent --plan-only` 能输出任务拆解、关键文件、证据、修改建议、测试建议。
- 证据不足时明确提示不足，不编造文件或函数。
- 输出可以直接作为下一步开发任务说明。

## 建议给 Claude Code 的执行提示

可以把下面这段直接发给 Claude Code：

```text
请按照 E:\code\Code_RAG\plan.md 重构 Code_RAG。目标是把当前本地代码库 RAG CLI 升级为支持本地路径和 GitHub/Git 仓库 URL 的代码库智能助手，并增加只读的轻量 Code Agent 能力。

执行要求：
1. 先阅读 plan.md、CLAUDE.md、README.md、PROJECT_STATUS.md、src/code_rag/cli.py、src/code_rag/services/*、tests/*。
2. 不要 reset 或丢弃现有未提交改动。
3. 先建立 repository source abstraction，再改 IndexService/QueryService/ManifestService/CLI。
4. 远程 Git 测试必须使用本地临时 git repo 或 bare repo，不依赖真实 GitHub 网络。
5. 保持现有本地路径命令兼容。
6. 每个 phase 完成后运行 pytest 和 ruff，失败时先修复再继续。
7. 不要提交 .env、ChromaDB 数据、模型缓存、远程仓库缓存或临时 debug 文件。

最终交付：
- 支持 local path 和 Git URL 的 index/search/ask/status/remove。
- cache list/prune 命令。
- agent --plan-only 命令。
- 完整测试。
- README 和 PROJECT_STATUS 更新。
- 通过：
  uv run --frozen pytest tests/ -v
  uv run --frozen ruff check src/ tests/
  uv run --frozen ruff format --check src/ tests/
```

## 推荐优先级

最高优先级：

1. `repository/` 输入源抽象。
2. Git URL clone/cache/refresh。
3. 稳定 collection key 和 manifest。
4. CLI 从 `repo_path` 升级为 `source`。
5. 离线测试覆盖远程仓库场景。

中等优先级：

1. `cache list/prune`。
2. `agent --plan-only`。
3. README 演示和简历描述。

低优先级：

1. 自动生成 patch。
2. Web UI。
3. 私有仓库 token 认证。
4. 多用户服务化部署。
