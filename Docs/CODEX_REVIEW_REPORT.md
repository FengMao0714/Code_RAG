\# 代码知识库 RAG 项目测试与完整性评估报告



\## 1. 项目当前状态



结论：\*\*基本可运行但有问题\*\*。



项目已经具备完整 CLI 闭环：`scanner → parser → chunker → embedder → ChromaDB → retriever → LLM`。我用临时仓库实际跑通了从零索引、状态查看、提问、删除索引，LLM 能基于代码回答并引用文件行号。



但它还不适合直接当“稳定成熟项目”展示，主要问题是：\*\*没有任何自动化测试、Windows 默认终端会因 emoji 输出崩溃、ChromaDB collection 不存在时状态/删除逻辑会抛异常、检索质量不稳定\*\*。作为简历项目可以展示，但建议先补掉高优先级问题和测试。



真实结构：



\- CLI 入口：\[cli.py](E:/code/Code\_RAG/src/code\_rag/cli.py)，`pyproject.toml` 定义 `code-rag = "code\_rag.cli:app"`

\- 配置：\[config.py](E:/code/Code\_RAG/src/code\_rag/config.py)

\- 索引：`scanner.py / parser.py / chunker.py / embedder.py`

\- 存储：`vector\_store.py / index\_tracker.py`

\- 检索：`retriever.py`

\- 生成：`llm.py / prompts.py`

\- `tests/` 目录存在但为空

\- README/CLAUDE/FINAL\_REPORT 存在，但 README 提到的 `utils/file\_utils.py` 实际不存在



\## 2. 实际执行过的命令



| 命令 | 结果 | 摘要 |

|---|---:|---|

| `uv run --frozen python --version` | 成功 | 项目环境 Python `3.12.13` |

| `uv run --frozen python -c "import ..."` | 成功 | 核心依赖 `chromadb/sentence\_transformers/tree\_sitter/openai/typer/rich/tiktoken` 可导入 |

| `uv run --frozen pytest -q` | 失败 | `no tests ran`，当前没有可运行测试 |

| `uv run --frozen code-rag --help` | 成功 | CLI 命令存在：`index/ask/chat/list/status/remove` |

| `uv run --frozen code-rag status .` | 失败 | Windows GBK 下 emoji 导致 `UnicodeEncodeError` |

| `$env:PYTHONIOENCODING='utf-8'; uv run --frozen code-rag status .` | 成功 | 当前仓库已有索引：204 chunks |

| `$env:PYTHONIOENCODING='utf-8'; uv run --frozen code-rag index .` | 成功 | 扫描 36 文件，检测无变更 |

| `$env:PYTHONIOENCODING='utf-8'; uv run --frozen code-rag ask . "这个项目的 CLI 入口在哪里？"` | 成功但效果差 | 检索只命中文档，LLM 回答“无法确定入口”，实际 `cli.py` 被默认阈值过滤 |

| 自建临时仓库 `code-rag index/status/ask/remove` | 成功 | 从零生成 5 chunks，并正确回答 `add` 方法用途 |

| 自写临时模块验证脚本 | 部分成功 | scanner/parser/chunker/tracker/vector\_store 正常路径可用；发现 Chroma missing collection 异常 |

| `uv run --frozen ruff check src/` | 成功 | 代码静态检查通过 |



\## 3. 核心功能完整性检查表



| 模块 | 是否存在 | 是否能运行 | 问题 | 优先级 |

|---|---:|---:|---|---|

| scanner | 是 | 是 | 能过滤 `.git/\_\_pycache\_\_/node\_modules/venv/dist/build`，Windows 路径正常；但 `.toml` 等关键配置不会入库 | 中 |

| parser | 是 | 是 | Python class/function 可解析；语法错误文件不崩溃，但会把错误代码也解析出符号，未检查 `root.has\_error` | 中 |

| chunker | 是 | 是 | 能生成 `module\_summary/class/function/doc`，长函数可二次切分；class chunk 可能包含方法体，和设计“不含方法体”不一致 | 中 |

| embedder | 是 | 是 | BGE 模型可加载，批量 embedding 维度一致，实测 `1024`；依赖 HF 网络/缓存，首次慢 | 中 |

| vector\_store | 是 | 部分 | upsert/query/delete\_by\_files 可用；不存在 collection 时 `get\_stats/delete\_collection` 抛 `NotFoundError` | 高 |

| index\_tracker | 是 | 是 | 新增/修改/删除均可识别，能避免无变更重复索引 | 低 |

| retriever | 是 | 是 | top\_k/threshold 可配；默认阈值 `0.5` 实测会过滤掉相关代码 chunk，导致回答偏差 | 高 |

| rag\_chain / answer\_generator | 部分 | 是 | 无独立 `rag\_chain`，由 `Retriever + LLMClient` 组成；LLM 调用可用，引用来源依赖 prompt 和召回质量 | 中 |

| CLI / API | CLI 有，API 无 | 部分 | CLI 可跑；Windows 默认编码会崩；未索引仓库 `status` 会异常 | 高 |

| tests | 目录有 | 否 | `pytest` 无测试可运行 | 高 |

| README | 是 | 是 | 基本准确；提到 `utils/file\_utils.py` 但实际缺失 | 低 |



\## 4. 已发现的问题



高优先级问题：



1\. \*\*没有自动化测试\*\*：`pytest` 直接 `no tests ran`，简历展示时这是最大短板之一。

2\. \*\*Windows 默认终端会崩溃\*\*：\[cli.py](E:/code/Code\_RAG/src/code\_rag/cli.py:350) 等多处直接输出 emoji，GBK 下触发 `UnicodeEncodeError`。

3\. \*\*ChromaDB collection 不存在时异常未兼容\*\*：\[vector\_store.py](E:/code/Code\_RAG/src/code\_rag/store/vector\_store.py:139) 和 \[vector\_store.py](E:/code/Code\_RAG/src/code\_rag/store/vector\_store.py:314) 只捕获 `ValueError`，当前 Chroma 抛 `NotFoundError`。

4\. \*\*真实问答召回不稳定\*\*：问“CLI 入口在哪里”，`src/code\_rag/cli.py` 分数 `0.529`，被默认阈值 `0.5` 过滤，LLM 最终答错。



中优先级问题：



1\. `pyproject.toml` 不入库，导致 CLI 入口这类问题缺少 `\[project.scripts]` 证据。

2\. parser 对语法错误文件不崩溃，但也不标记错误，可能污染索引。

3\. class chunk 的“只保留方法签名”逻辑有问题，可能保留方法体。

4\. Chroma 中保存了 `extra\_metadata`，但 query 重建 `CodeChunk` 时丢失扩展 metadata。

5\. `chat` 更像循环单轮问答，没有保留对话历史。



低优先级问题：



1\. README 文档和实际文件有轻微不一致。

2\. 首次加载 embedding 会产生大量 HuggingFace 日志，CLI 体验偏吵。

3\. `list` 只显示仓库 hash 和文件数，不显示原始路径，展示体验一般。



\## 5. 当前功能是否完整



明确回答：



\- \*\*是否具备最小闭环？\*\* 是。

\- \*\*是否能从“扫描代码 → 切片 → embedding → 入库 → 检索 → 回答”完整跑通？\*\* 能。我用临时仓库实际跑通了。

\- \*\*卡点在哪里？\*\* 不是主流程卡死，而是边界和质量问题：Windows 编码、未索引仓库状态、missing collection、召回阈值、缺测试。

\- \*\*是否适合简历展示？\*\* 技术链路是完整的，适合作为简历项目基础；但建议先修高优先级问题并补测试，否则面试深挖时会显得“能 demo，但工程化不足”。



\## 6. 建议补充的测试



建议最少补这些：



\- 单元测试：scanner 过滤、parser Python 解析、chunker metadata/长函数切分、index\_tracker 增删改。

\- 集成测试：vector\_store upsert/query/delete/reset。

\- 端到端测试：用 monkeypatch fake embedder/LLM 跑 `code-rag index` 和 `ask`。

\- 边界测试：语法错误文件、空文件、大文件、未索引仓库 status、Windows 路径、collection 不存在。



示例 pytest，不要现在直接改文件，可以后续放到 `tests/test\_core.py`：



```python

from pathlib import Path



from code\_rag.indexer.scanner import RepoScanner

from code\_rag.indexer.parser import CodeParser

from code\_rag.indexer.chunker import CodeChunker

from code\_rag.store.index\_tracker import IndexTracker

from code\_rag.config import Settings





def test\_scanner\_filters\_default\_dirs(tmp\_path: Path):

&#x20;   (tmp\_path / "node\_modules").mkdir()

&#x20;   (tmp\_path / "node\_modules" / "x.py").write\_text("def bad(): pass")

&#x20;   (tmp\_path / "app.py").write\_text("def ok(): return 1")



&#x20;   entries = RepoScanner(tmp\_path).scan()



&#x20;   assert \[e.rel\_path for e in entries] == \["app.py"]





def test\_parser\_and\_chunker\_python(tmp\_path: Path):

&#x20;   file = tmp\_path / "app.py"

&#x20;   file.write\_text("class A:\\n    def f(self):\\n        return 1\\n\\ndef g():\\n    return 2\\n")



&#x20;   symbols = CodeParser().parse\_file(file, "python", "app.py")

&#x20;   chunks = CodeChunker(max\_chunk\_tokens=64).chunk(

&#x20;       symbols, "hash", full\_source=file.read\_text()

&#x20;   )



&#x20;   assert any(c.chunk\_type == "module\_summary" for c in chunks)

&#x20;   assert any(c.chunk\_type == "class" and c.name == "A" for c in chunks)

&#x20;   assert any(c.chunk\_type == "function" and c.name == "g" for c in chunks)





def test\_index\_tracker\_detects\_changes(tmp\_path: Path):

&#x20;   file = tmp\_path / "a.py"

&#x20;   file.write\_text("x = 1")



&#x20;   settings = Settings(index\_tracker\_dir=str(tmp\_path / ".indexes"))

&#x20;   tracker = IndexTracker(settings)



&#x20;   entries1 = RepoScanner(tmp\_path).scan()

&#x20;   assert len(tracker.get\_changes(tmp\_path, entries1).added) == 1



&#x20;   tracker.update\_tracker(tmp\_path, entries1)

&#x20;   file.write\_text("x = 2")

&#x20;   entries2 = RepoScanner(tmp\_path).scan()



&#x20;   changes = tracker.get\_changes(tmp\_path, entries2)

&#x20;   assert \[e.rel\_path for e in changes.modified] == \["a.py"]

```



\## 7. 下一步修改建议



第一阶段：先修复影响运行的问题



\- 处理 Windows 输出编码或去掉 CLI emoji。

\- 兼容 ChromaDB `NotFoundError`，让未索引仓库 `status` 正常显示“尚未索引”。

\- 调整默认检索阈值，或先取 top\_k 再交给上下文排序，避免相关代码被过滤。

\- 把 `.toml/.yaml/.json` 等项目配置文件作为 doc/config chunk 入库。



第二阶段：补齐核心测试



\- 先补 scanner/parser/chunker/tracker/vector\_store 单元测试。

\- 再补 CLI 端到端测试，用 fake embedding 和 fake LLM 避免真实网络。

\- CI 至少跑 `ruff check` + `pytest`。



第三阶段：优化 RAG 效果



\- 增加 hybrid retrieval：向量检索 + 文件名/符号名关键词召回。

\- 对问题中出现的 `cli.py`、`pyproject`、函数名、类名做 metadata boost。

\- 上下文里加入文件路径、符号名、score，方便 LLM 引用。

\- 支持 `search` 命令，先看召回再问答。



第四阶段：完善简历展示内容



\- README 增加架构图、端到端 Demo、测试截图、已知限制。

\- 补一段“为什么按 AST 语义切片，而不是固定长度切片”。

\- 展示增量索引效果：首次索引、修改一个文件、只更新变更文件。

\- 加一个小型示例仓库和固定 demo 命令，保证面试现场稳定演示。



本轮没有修改项目代码，最后检查 `git status --short` 仍然是干净的。

