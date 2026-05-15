"""配置管理模块。

从 .env 文件加载所有配置项，使用 pydantic-settings 实现类型安全。
"""

import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """应用配置，从环境变量和 .env 文件加载。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- LLM 配置 ----------
    llm_base_url: str = "https://api.siliconflow.cn/v1"
    llm_api_key: str = "your-api-key-here"
    llm_model: str = "XiaomiMiMo/MiMo-7B-RL"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.6

    # ---------- Embedding 配置 ----------
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_cache_dir: str | None = None

    # ---------- ChromaDB 配置 ----------
    chroma_persist_dir: str = "~/.code-rag/chroma"

    # ---------- 检索配置 ----------
    retrieval_top_k: int = 8
    retrieval_score_threshold: float = 0.3

    # ---------- 索引配置 ----------
    index_tracker_dir: str = "~/.code-rag/indexes"
    max_chunk_tokens: int = 512

    @property
    def chroma_persist_path(self) -> Path:
        """ChromaDB 持久化目录的绝对路径。"""
        return Path(self.chroma_persist_dir).expanduser().resolve()

    @property
    def index_tracker_path(self) -> Path:
        """索引追踪数据目录的绝对路径。"""
        return Path(self.index_tracker_dir).expanduser().resolve()


def get_settings() -> Settings:
    """获取配置实例。"""
    return Settings()
