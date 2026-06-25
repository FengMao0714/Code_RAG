# PROJECT_STATUS.md — Code_RAG 项目状态

> 最后更新：2026-06-05（第五阶段完成：本地 + Git 仓库 + 轻量 Code Agent）

## 项目概述

代码知识库 RAG + 轻量 Code Agent CLI。基于 tree-sitter AST 语义切片、BGE 本地 Embedding、
ChromaDB 向量存储、符号词法召回与 RRF 混合排序。
**同一套命令同时支持本地路径与 Git 仓库 URL**，并提供只读的 Code Agent 能力。
完整闭环：

```
本地 / Git 仓库
  → resolve_repo → scanner → parser → chunker → embedder → ChromaDB
  → HybridRetrieval(vector + lexical + RRF) → ContextBuilder → LLM
  → 或 → CodeAgent(Planner + Retriever + 离线 Reasoner/Reviewer)
```

---

## 当前已完成的重构阶段

### 第五阶段（2026-06-05）— 本地 + Git 仓库 + 轻量 Code Agent

#### 目标

把已有 Code_RAG 从"只能索引本地路径"升级为"本地 + Git 仓库 URL"统一入口，
并新增只读轻量 Code Agent：`agent` 命令把自然语言任务拆解 → 检索 → 证据汇总
→ 输出结构化修改计划（不自动改文件）。

#### 新增模块

| 路径 | 作用 |
|------|------|
| `src/code_rag/repository/__init__.py` | 仓库源抽象入口 |
| `src/code_rag/repository/models.py` | `RepoSource` / `RepoIdentity` / `ResolvedRepo` |
| `src/code_rag/repository/parser.py` | 本地路径 / Git URL 解析 |
| `src/code_rag/repository/local.py` | 本地路径 provider |
| `src/code_rag/repository/git.py` | Git clone / fetch / checkout（GitPython） |
| `src/code_rag/repository/cache.py` | 远程仓库缓存目录 + 稳定 `collection_key` |
| `src/code_rag/repository/resolver.py` | `resolve_repo` 统一入口 |
| `src/code_rag/agent/__init__.py` | Agent 模块入口 |
| `src/code_rag/agent/models.py` | `AgentTask` / `AgentStep` / `AgentPlan` / `AgentReport` |
| `src/code_rag/agent/planner.py` | Planner：把任务拆成 3~6 个子问题 |
| `src/code_rag/agent/code_agent.py` | CodeAgent：plan → 检索 → 证据汇总 → 离线 Reasoner/Reviewer |

#### 新增 CLI 命令

```bash
uv run code-rag index  <source> [--ref <git-ref>] [--refresh]   # 同一命令支持本地/Git
uv run code-rag ask    <source> "<问题>"  [--ref <git-ref>]
uv run code-rag chat   <source> [--ref <git-ref>]
uv run code-rag search <source> "<问题>" --mode vector|lexical|hybrid
uv run code-rag status <source> [--ref <git-ref>]               # git 仓库展示 URL/ref/commit/cache
uv run code-rag remove <source> --yes [--with-cache]            # 可选同步删远程缓存
uv run code-rag eval   <source> --dataset ... --mode vector|lexical|hybrid
uv run code-rag agent  <source> "<任务>" [--plan-only]          # 轻量 Code Agent
uv run code-rag cache list                                      # 列出所有远程仓库缓存
uv run code-rag cache prune --yes                               # 清理所有远程仓库缓存
```

#### 仓库源抽象

- `RepoSource` 描述用户输入（`raw` / `kind=local|git` / `ref`）。
- `RepoIdentity` 携带规范化信息（`source_type` / `canonical_source` /
  `collection_key` / `ref` / `commit`）。
- `ResolvedRepo` 把仓库折叠为 `root_path` + `cache_path`（git 可选），
  供 scanner / retriever / manifest 统一使用。
- 稳定 `collection_key`：
  - 本地路径：12 位 SHA-256（与老 `get_collection_name` 完全相同，老索引不会失效）。
  - Git URL：`git-<8hex>-<ref>`，按 ref 隔离。
- 缓存目录：`<base_name>_<8hex>/{worktree,metadata.json}`，避免 Windows 260 字符路径限制。

#### 轻量 Code Agent

- **Planner**：按句号 / 问号 / 感叹号 / 分号切分任务句；不足 3 条时用
  架构 / 关键文件 / 风险 / 测试 等角度兜底；超过 6 条时截断。每条子问题附 rationale。
- **Retriever**：复用 `HybridRetriever`（vector + lexical + RRF），每个子问题召回 top_k=5。
- **Evidence Builder**：按 `file_path` 聚合证据，按命中次数生成关键文件列表。
- **Reasoner**：离线模板生成 understanding / suggested_changes / risks /
  suggested_tests（命中"性能"等关键词时追加 benchmark 建议）。
- **Reviewer**：所有 evidence 为空时标 `insufficient_evidence=True`，
  拒绝臆测并附"证据不足，建议补充信息"备注。

`agent` 输出固定结构：任务理解 / 计划拆解 / 关键文件 / 修改建议（只读）/
风险点 / 建议运行的测试 / 引用证据 / Reviewer 备注。

#### 新增测试

| 文件 | 测试数 | 覆盖点 |
|------|--------|--------|
| `tests/test_repository.py` | 34 | 解析 / 缓存 / 本地 / Git（local bare repo） / resolver |
| `tests/test_cli_remote.py` | 5 | `cache list` / `cache prune` / 远程状态 / 端到端生命周期 |
| `tests/test_agent.py` | 9 | Planner 拆解、CodeAgent 端到端、`--plan-only` CLI 输出 |

测试总数：**153 → 201**

#### 设计原则

- **本地路径完全向后兼容**：所有现有命令保持老行为，老索引不需重建。
- **远程测试零网络依赖**：用本地 bare repo 模拟 Git URL，避免访问 GitHub。
- **只读 Agent**：明确不调用 LLM、不修改文件，所有结论只作为修改建议。
- **缓存可清理**：`cache prune` 支持一键清理所有远程仓库克隆（不进入 git 历史）。

---

### 第四阶段（2026-06-02）— AI/RAG 实习简历项目重构

#### 新增模块

| 路径 | 作用 |
|------|------|
| `src/code_rag/services/__init__.py` | 服务层入口 |
| `src/code_rag/services/index_service.py` | 索引流程编排（CLI 不再做业务） |
| `src/code_rag/services/query_service.py` | 检索 + 低置信度评估 |
| `src/code_rag/services/manifest_service.py` | Manifest 读写 / list / status |
| `src/code_rag/services/eval_service.py` | 评测编排（不调用 LLM） |
| `src/code_rag/retriever/lexical.py` | 词法检索（符号/文件/源码子串） |
| `src/code_rag/retriever/rerank.py` | RRF 重排序（k=60，通道权重） |
| `src/code_rag/retriever/hybrid.py` | 向量 + 词法 + RRF 融合 |
| `src/code_rag/evaluation/__init__.py` | 评测模块入口 |
| `src/code_rag/evaluation/dataset.py` | Golden query YAML 加载 |
| `src/code_rag/evaluation/metrics.py` | Recall@1/3/8、MRR、hit rate |
| `src/code_rag/evaluation/report.py` | JSON + Markdown 报告 |
| `evals/code_rag_golden.yaml` | 12 条 golden query（10 类） |
| `.github/workflows/ci.yml` | Windows / Linux 跑 ruff + pytest |

#### 检索流程

```
问题 → Embedder
     → Vector Channel (Retriever: top_k×5 候选 + metadata boost + 阈值过滤)
     → Lexical Channel (LexicalRetriever: 符号/文件/源码子串)
     → RRFReranker (k=60, 通道权重)
     → ContextBuilder (含 score / file / lines / language)
     → LLM streaming
```

#### 低置信度判定

`QueryService._evaluate_confidence`：

1. 上下文为空 → 低置信度（未找到任何相关代码片段）
2. 所有 chunk 都属于 doc（README/CONFIG）→ 低置信度（未命中代码）
3. 平均距离 > 1.0 → 低置信度（结果可能不相关）

`ask` / `chat` 在生成前打印提示，不诱导 LLM 硬答。

---

## 检索评测结果（baseline）

在 `evals/code_rag_golden.yaml`（12 条 query）上跑出的 baseline：

| 模式 | Recall@1 | Recall@3 | Recall@8 | MRR | file_hit | symbol_hit |
|------|----------|----------|----------|------|----------|------------|
| vector | 41.67% | 50.00% | 66.67% | 0.5000 | 58.33% | 58.33% |
| hybrid | 50.00% | 58.33% | 66.67% | 0.5486 | 58.33% | 50.00% |

**hybrid 模式相对 vector 模式**：Recall@1 +8.3pp，Recall@3 +8.3pp，MRR +0.05。

`web_ui_negative` 是负样本，期望 `expected_files=[]`，evaluator 不会因为没命中而误判；
实际未命中符合预期（说明 evaluator 行为正确）。

---

## 能力矩阵

| 能力 | 实现位置 | 状态 |
|------|----------|------|
| 14 种语言 AST 语义切片 | `indexer/parser.py` / `chunker.py` | ✅ |
| 增量索引 + SHA-256 追踪 | `store/index_tracker.py` | ✅ |
| Manifest 记录 + list / status | `services/manifest_service.py` | ✅ |
| 向量检索 + metadata boost | `retriever/retriever.py` | ✅ |
| 词法检索（符号/文件/源码） | `retriever/lexical.py` | ✅ |
| Hybrid（vector + lexical + RRF） | `retriever/hybrid.py` | ✅ |
| Golden query 评测 + JSON / Markdown 报告 | `evaluation/*` | ✅ |
| OpenAI 兼容流式问答 | `generator/llm.py` | ✅ |
| 低置信度评估 | `services/query_service.py` | ✅ |
| 本地路径 / Git URL 统一入口 | `repository/*` | ✅ |
| 远程仓库缓存 + cache list / prune | `repository/cache.py` / `cli.py` | ✅ |
| 轻量 Code Agent（只读） | `agent/*` | ✅ |
| Windows / Linux CI | `.github/workflows/ci.yml` | ✅ |
| 私有 Git 仓库 token 鉴权 | — | ⏳（`allow_private_git` 已留位，token 流程未实现） |
| 自动应用修改（写文件） | — | ❌（只读 Agent，符合安全设计） |

---

## 推荐简历表达

开发代码仓库 RAG + 轻量 Code Agent CLI，基于 tree-sitter 实现 14 种语言
AST 语义切片，结合 BGE Embedding、ChromaDB、符号词法索引与 RRF 混合召回；
抽象出 RepoSource / RepoIdentity / ResolvedRepo 仓库源模型，**同一套命令支持
本地路径与 Git 仓库 URL**，并通过 Planner → Hybrid Retriever → 证据汇总 →
离线 Reasoner/Reviewer 输出只读修改计划；构建 golden query 评测集统计
Recall@k/MRR，并通过 283 个自动化测试和 Windows/Linux CI 保障质量。

---

## 已保留的旧阶段

- **第一阶段**（2026-05-16）：Windows GBK / ChromaDB NotFoundError / 检索阈值 / 配置文件索引
- **第二阶段**（2026-05-16）：补齐 87 个核心测试
- **第三阶段**（2026-05-16）：metadata boost 检索优化 + 28 个 retriever 测试
