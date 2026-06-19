"""服务层模块。

将 CLI 命令中的业务编排逻辑下沉到 ``services`` 包，
让 CLI 只负责参数解析和 Rich 展示。

主要组件：

- :class:`IndexService`: 索引流程编排（扫描、变更检测、解析、切片、Embedding、入库）。
- :class:`QueryService`: 检索流程编排（向量检索、metadata boost、上下文组装、LLM 调用）。
- :class:`ManifestService`: 仓库 manifest 读写、状态展示。
"""

from code_rag.services.eval_service import EvalOptions, EvalService
from code_rag.services.index_service import IndexResult, IndexService
from code_rag.services.manifest_service import ManifestEntry, ManifestService
from code_rag.services.query_service import QueryService, build_retriever

__all__ = [
    "EvalOptions",
    "EvalService",
    "IndexResult",
    "IndexService",
    "ManifestService",
    "ManifestEntry",
    "QueryService",
    "build_retriever",
]
