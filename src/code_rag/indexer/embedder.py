"""Embedding 生成模块。

使用 sentence-transformers 加载本地 Embedding 模型（默认 BAAI/bge-large-zh-v1.5），
为代码切片生成向量表示。模型在首次调用时懒加载，全局单例复用。
"""

from __future__ import annotations

import logging
import time

from code_rag.config import Settings, get_settings

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
        self._model_name = self._settings.embedding_model
        self._device = self._settings.embedding_device

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
            cls._instance._settings = settings
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（用于测试）。"""
        cls._instance = None

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

        kwargs: dict = {}
        if self._settings.embedding_cache_dir:
            kwargs["cache_folder"] = self._settings.embedding_cache_dir

        try:
            logger.info(
                "正在加载 Embedding 模型: %s (device=%s)",
                self._model_name,
                self._device,
            )
            self._model = SentenceTransformer(self._model_name, device=self._device, **kwargs)
            logger.info("Embedding 模型加载完成")
        except (OSError, RuntimeError) as exc:
            if self._device == "cpu":
                raise RuntimeError(f"模型加载失败: {exc}") from exc
            logger.warning("设备 '%s' 加载失败，回退到 CPU: %s", self._device, exc)
            self._model = SentenceTransformer(self._model_name, device="cpu", **kwargs)
            self._device = "cpu"
            logger.info("已回退到 CPU 设备")

    def _encode(self, texts: list[str]) -> list[list[float]]:
        """底层编码方法，直接调用 SentenceTransformer.encode()。

        Args:
            texts: 待编码的文本列表。

        Returns:
            嵌入向量列表（每个向量为 ``list[float]``）。
        """
        if self._model is None:
            self._load_model()

        assert self._model is not None  # 类型守卫
        embeddings = self._model.encode(
            texts,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return [vec.tolist() for vec in embeddings]

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
            results.extend(self._encode(batch))

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
        return self.embed_texts([text])[0]
