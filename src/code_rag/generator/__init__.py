"""生成模块。

提供 LLM 调用封装和 Prompt 模板管理，负责将检索到的上下文
与用户问题组合，调用 LLM 生成回答。
"""

from code_rag.generator.llm import LLMClient, StreamingChunk

__all__ = ["LLMClient", "StreamingChunk"]
