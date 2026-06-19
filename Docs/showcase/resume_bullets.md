# Resume Bullets

## 中文版

- 开发代码仓库 RAG CLI，基于 tree-sitter 实现 14 种语言 AST 语义切片，将函数、类、模块和文档切分为可检索 chunk，并用 SHA-256 追踪实现增量索引。
- 构建 Hybrid Retrieval：结合 BGE Embedding + ChromaDB 向量召回、文件名/符号词法召回与 RRF 融合排序，支持 `vector / lexical / hybrid` 多模式对比和可解释检索调试。
- 设计 golden query 评测体系，覆盖符号定位、流程解释、负样本、安全边界和 Agent 报告导出，自动生成 Recall@k、MRR、file hit、symbol hit、latency 的 JSON/Markdown 报告。
- 抽象本地路径与 Git URL 仓库源，支持远程仓库缓存、ref checkout、token 脱敏，并保证未索引远程 `search/status/remove` 不触发 clone/fetch。
- 实现只读 Code Agent，将任务拆解为子问题，通过 Hybrid 检索聚合证据，输出关键文件、修改建议、风险和建议测试，并支持 Markdown/JSON 报告导出。
- 建立 263 个自动化测试覆盖 scanner、parser、chunker、retriever、vector store、repository、CLI、evaluation 和 agent，配合 ruff lint/format 保证工程质量。

## English Version

- Built a codebase RAG CLI that indexes local paths and Git repositories using tree-sitter AST parsing across 14 languages, semantic chunking, BGE embeddings, and ChromaDB persistence.
- Implemented hybrid retrieval with vector search, lexical symbol/file recall, and Reciprocal Rank Fusion, with unified `vector / lexical / hybrid` modes for search, QA, chat, and evaluation.
- Designed a golden-query evaluation suite that reports Recall@k, MRR, file hit rate, symbol hit rate, and latency to JSON/Markdown for reproducible retrieval quality tracking.
- Added repository-source abstraction for local paths and Git URLs, including cache management, ref checkout, URL credential redaction, unsafe scheme rejection, and no-clone behavior for unindexed read-only commands.
- Implemented a read-only Code Agent that decomposes tasks, retrieves evidence, ranks key files, surfaces risks, recommends tests, and exports Markdown/JSON reports.
- Maintained strong engineering coverage with 263 automated tests plus ruff lint/format checks across indexing, retrieval, repository, CLI, evaluation, and agent workflows.

## 面试讲解重点

1. 为什么代码 RAG 不能只靠固定长度切片：AST 语义边界能保留函数/类上下文。
2. 为什么需要 lexical 通道：中文问题问 `CLI 入口在哪里` 时，向量相似度可能不如 `cli.py` / `app` 精确命中稳定。
3. 为什么要做 eval：RAG 优化如果没有 golden query，很容易凭主观感觉调参。
4. 为什么 Agent 只读：代码修改需要更强验证链，先做证据定位和风险提示更安全。
5. 为什么强调无副作用命令：调试检索或查看状态不应偷偷 clone/fetch 远程仓库。
