"""检索模块。

提供向量检索和上下文组装功能，将用户问题转换为检索查询，
从 ChromaDB 中召回最相关的代码切片，并格式化为 LLM 可消费的上下文。
"""

from code_rag.retriever.modes import SearchMode
from code_rag.retriever.retriever import ContextBuilder, RetrievalResult, Retriever

__all__ = ["Retriever", "ContextBuilder", "RetrievalResult", "SearchMode"]
