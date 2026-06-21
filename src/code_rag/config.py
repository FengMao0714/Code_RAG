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
    embedding_profile: str = "baseline"
    """内置 embedding profile ID；可选 baseline / bge-m3 / e5-base / custom。"""
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_cache_dir: str | None = None
    embedding_offline: bool = False
    """是否只从本地路径或 Hugging Face 缓存加载 Embedding 模型。"""
    embedding_query_prefix: str | None = None
    """查询文本前缀；为 None 时使用 profile 默认值。"""
    embedding_document_prefix: str | None = None
    """文档/chunk 文本前缀；为 None 时使用 profile 默认值。"""

    # ---------- ChromaDB 配置 ----------
    chroma_persist_dir: str = "~/.code-rag/chroma"

    # ---------- 检索配置 ----------
    retrieval_top_k: int = 8
    retrieval_score_threshold: float = 0.7

    # ---------- 索引配置 ----------
    index_tracker_dir: str = "~/.code-rag/indexes"
    max_chunk_tokens: int = 512

    # ---------- 远程仓库 / 缓存配置 ----------
    repo_cache_dir: str = "~/.code-rag/repos"
    """远程 git 仓库的本地缓存根目录。"""
    git_clone_depth: int = 1
    """``git clone --depth`` 值；``0`` 表示完整克隆。"""
    allow_private_git: bool = False
    """是否允许私有仓库（默认 False，token 鉴权尚未实现）。"""
    allow_file_remote: bool = False
    """是否允许 file:// 协议的远程仓库（默认 False，仅测试使用）。"""

    @property
    def chroma_persist_path(self) -> Path:
        """ChromaDB 持久化目录的绝对路径。"""
        return Path(self.chroma_persist_dir).expanduser().resolve()

    @property
    def index_tracker_path(self) -> Path:
        """索引追踪数据目录的绝对路径。"""
        return Path(self.index_tracker_dir).expanduser().resolve()

    @property
    def repo_cache_path(self) -> Path:
        """远程仓库本地缓存目录的绝对路径。"""
        return Path(self.repo_cache_dir).expanduser().resolve()


def get_settings() -> Settings:
    """获取配置实例。"""
    return Settings()
