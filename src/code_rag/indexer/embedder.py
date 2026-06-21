"""Embedding 生成模块。

使用 sentence-transformers 加载本地 Embedding 模型（默认 BAAI/bge-large-zh-v1.5），
为代码切片生成向量表示。模型在首次调用时懒加载，全局单例复用。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from code_rag.config import Settings, get_settings
from code_rag.embedding_profiles import EmbeddingProfile, resolve_embedding_profile

logger = logging.getLogger(__name__)


class Embedder:
    """本地 Embedding 生成器（单例模式）。

    使用 sentence-transformers 加载模型，在首次调用时初始化。
    后续调用复用已加载的模型实例，避免重复加载开销。

    用法::

        embedder = Embedder.get_instance()
        vectors = embedder.embed_texts(["hello world", "foo bar"])
        single = embedder.embed_query("what is this?")

    配置通过 :class:`Settings` 管理：

    - ``embedding_model``: HuggingFace 模型名称
    - ``embedding_profile``: 内置 profile ID 或 custom
    - ``embedding_device``: 运行设备 (cpu / cuda / mps)
    - ``embedding_cache_dir``: 模型缓存目录（可选）
    """

    _instance: Embedder | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化 Embedder。

        Args:
            settings: 应用配置；为 ``None`` 时使用默认配置。
        """
        self._settings = settings or get_settings()
        self._model = None  # SentenceTransformer 实例（延迟加载）
        self._profile: EmbeddingProfile
        self._model_name: str
        self._device: str
        self._query_prefix: str
        self._document_prefix: str
        self._configure(self._settings)

    @classmethod
    def get_instance(cls, settings: Settings | None = None) -> Embedder:
        """获取全局单例实例。

        首次调用时创建实例，后续调用返回已有实例。
        如果传入 ``settings`` 且与已有实例不同，会更新配置。

        Args:
            settings: 应用配置。

        Returns:
            :class:`Embedder` 单例。
        """
        if cls._instance is None:
            cls._instance = cls(settings)
            logger.info("已创建 Embedder 单例 (model=%s)", cls._instance._model_name)
        elif settings is not None:
            cls._instance._configure(settings)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（用于测试）。"""
        cls._instance = None

    def _configure(self, settings: Settings) -> None:
        """Apply settings and reset loaded model when the profile changes."""
        profile = resolve_embedding_profile(settings)
        old_model_name = getattr(self, "_model_name", None)
        old_device = getattr(self, "_device", None)
        old_cache_dir = getattr(self, "_settings", settings).embedding_cache_dir
        old_offline = getattr(self, "_settings", settings).embedding_offline

        self._settings = settings
        self._profile = profile
        self._model_name = profile.model_name
        self._device = settings.embedding_device
        self._query_prefix = profile.query_prefix
        self._document_prefix = profile.document_prefix

        if old_model_name is not None and (
            old_model_name != self._model_name
            or old_device != self._device
            or old_cache_dir != settings.embedding_cache_dir
            or old_offline != settings.embedding_offline
        ):
            self._model = None

    def _load_model(self) -> None:
        """加载 SentenceTransformer 模型（懒加载）。

        首次调用时从 HuggingFace Hub 或本地缓存加载模型。
        如果指定设备加载失败，自动回退到 CPU。

        Raises:
            RuntimeError: 模型加载完全失败时抛出。
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError("sentence-transformers 未安装。请执行: uv add sentence-transformers")

        try:
            logger.info(
                "正在加载 Embedding 模型: %s (device=%s)",
                self._model_name,
                self._device,
            )
            local_model_path = self._resolve_local_model_path()
            self._model = self._load_with_device(
                SentenceTransformer,
                device=self._device,
                local_files_only=True,
                model_name_or_path=local_model_path,
            )
            logger.info("Embedding 模型已从本地缓存加载")
            logger.info("Embedding 模型加载完成")
        except (OSError, RuntimeError) as local_exc:
            if self._settings.embedding_offline or self._looks_like_local_model_path():
                raise RuntimeError(f"本地 Embedding 模型加载失败: {local_exc}") from local_exc
            logger.info(
                "本地缓存未命中，尝试从 Hugging Face Hub 下载或补全模型: %s",
                self._model_name,
            )
            try:
                self._model = self._load_with_device(
                    SentenceTransformer,
                    device=self._device,
                    local_files_only=False,
                    model_name_or_path=self._model_name,
                )
                logger.info("Embedding 模型加载完成")
            except (OSError, RuntimeError) as exc:
                raise RuntimeError(f"模型加载失败: {exc}") from exc

    def _load_with_device(
        self,
        sentence_transformer_cls: Any,
        *,
        device: str,
        local_files_only: bool,
        model_name_or_path: str,
    ) -> Any:
        """按指定设备和离线策略加载 SentenceTransformer，必要时回退 CPU。"""
        kwargs = self._model_kwargs(local_files_only=local_files_only)
        previous_env = self._enable_hf_offline_env() if local_files_only else None
        try:
            try:
                return sentence_transformer_cls(model_name_or_path, device=device, **kwargs)
            except (OSError, RuntimeError):
                if device == "cpu":
                    raise
                logger.warning("设备 '%s' 加载失败，回退到 CPU", device)
                self._device = "cpu"
                return sentence_transformer_cls(model_name_or_path, device="cpu", **kwargs)
        except (OSError, RuntimeError):
            if previous_env is not None:
                self._restore_hf_env(previous_env)
            raise

    def _model_kwargs(self, *, local_files_only: bool) -> dict[str, Any]:
        """构造 SentenceTransformer 加载参数。"""
        kwargs: dict[str, Any] = {"local_files_only": local_files_only}
        if self._settings.embedding_cache_dir:
            kwargs["cache_folder"] = self._settings.embedding_cache_dir
        return kwargs

    def _looks_like_local_model_path(self) -> bool:
        """判断 embedding_model 是否看起来是本地模型路径。"""
        raw = self._model_name
        path = Path(raw).expanduser()
        return path.exists() or path.is_absolute() or raw.startswith((".", "~"))

    def _resolve_local_model_path(self) -> str:
        """把模型名解析成本地路径，避免 SentenceTransformer 再访问 Hub 元数据。"""
        path = Path(self._model_name).expanduser()
        if path.exists():
            return str(path.resolve())

        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise OSError("huggingface_hub 未安装，无法解析本地模型缓存") from exc

        try:
            return snapshot_download(
                repo_id=self._model_name,
                cache_dir=self._settings.embedding_cache_dir,
                local_files_only=True,
            )
        except Exception as exc:
            raise OSError(f"本地模型缓存未命中: {self._model_name}") from exc

    @staticmethod
    def _enable_hf_offline_env() -> dict[str, str | None]:
        """开启 Hugging Face 离线模式，并返回旧环境值。"""
        keys = (
            "HF_HUB_OFFLINE",
            "TRANSFORMERS_OFFLINE",
            "HF_DATASETS_OFFLINE",
            "HF_HUB_DISABLE_TELEMETRY",
        )
        previous = {key: os.environ.get(key) for key in keys}
        for key in keys:
            os.environ[key] = "1"
        return previous

    @staticmethod
    def _restore_hf_env(previous: dict[str, str | None]) -> None:
        """恢复 Hugging Face 相关环境变量。"""
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _encode(self, texts: list[str], *, prefix: str = "") -> list[list[float]]:
        """底层编码方法，直接调用 SentenceTransformer.encode()。

        Args:
            texts: 待编码的文本列表。

        Returns:
            嵌入向量列表（每个向量为 ``list[float]``）。
        """
        if self._model is None:
            self._load_model()

        assert self._model is not None  # 类型守卫
        encoded_texts = self._apply_prefix(texts, prefix)
        embeddings = self._model.encode(
            encoded_texts,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return [vec.tolist() for vec in embeddings]

    @staticmethod
    def _apply_prefix(texts: list[str], prefix: str) -> list[str]:
        """Apply an embedding prompt prefix without mutating caller input."""
        if not prefix:
            return texts
        return [f"{prefix}{text}" for text in texts]

    def embed_texts(
        self,
        texts: list[str],
        *,
        batch_size: int = 64,
    ) -> list[list[float]]:
        """批量生成文本嵌入。

        当 ``len(texts) > batch_size`` 时自动分批编码，
        避免内存溢出。

        Args:
            texts: 待嵌入的文本列表。
            batch_size: 每批编码的文本数量。

        Returns:
            与 ``texts`` 等长的嵌入向量列表。
        """
        if not texts:
            return []

        t0 = time.monotonic()
        results: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            results.extend(self._encode(batch, prefix=self._document_prefix))

        elapsed = time.monotonic() - t0
        logger.info(
            "Embedding 完成：%d 条文本，耗时 %.1f 秒 (%.1f 条/秒)",
            len(texts),
            elapsed,
            len(texts) / elapsed if elapsed > 0 else 0,
        )
        return results

    def embed_query(self, text: str) -> list[float]:
        """为单条查询文本生成嵌入。

        Args:
            text: 查询文本。

        Returns:
            嵌入向量。
        """
        return self._encode([text], prefix=self._query_prefix)[0]
