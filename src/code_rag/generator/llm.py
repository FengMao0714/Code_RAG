"""LLM 调用封装模块。

使用 OpenAI SDK 封装与 LLM 的交互，支持流式输出。
通过 ``config.py`` 的配置项（``llm_base_url``, ``llm_api_key``, ``llm_model``）
连接到 OpenAI 兼容的 API 服务。

主要组件：

- :class:`StreamingChunk`: 流式输出的单个 chunk 数据类。
- :class:`LLMClient`: LLM 客户端，封装 OpenAI SDK，支持同步和流式调用。
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from dataclasses import dataclass

from openai import OpenAI
from openai.types.chat import ChatCompletion

from code_rag.config import Settings, get_settings
from code_rag.generator.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 流式输出数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamingChunk:
    """流式输出的单个 chunk。

    Attributes:
        content: 本次 chunk 的文本内容。
        finish_reason: 完成原因（``None`` 表示尚未完成）。
        chunk_index: chunk 的序号（从 0 开始）。
    """

    content: str
    finish_reason: str | None
    chunk_index: int


# ---------------------------------------------------------------------------
# LLM 客户端
# ---------------------------------------------------------------------------


class LLMClient:
    """LLM 客户端。

    封装 OpenAI SDK，提供同步和流式两种调用方式。
    使用 ``config.py`` 中的配置连接到 OpenAI 兼容的 API 服务。

    用法::

        client = LLMClient()

        # 同步调用
        answer = client.generate(context="...", question="...")

        # 流式调用
        for chunk in client.generate_stream(context="...", question="..."):
            print(chunk.content, end="", flush=True)

    Args:
        settings: 应用配置；为 ``None`` 时使用默认配置。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化 LLM 客户端。

        Args:
            settings: 应用配置。

        Raises:
            ValueError: 当 API Key 未配置或为默认值时。
        """
        self._settings = settings or get_settings()

        # 验证 API Key
        if not self._settings.llm_api_key or self._settings.llm_api_key == "your-api-key-here":
            raise ValueError("LLM API Key 未配置。请在 .env 文件中设置 LLM_API_KEY。")

        # 创建 OpenAI 客户端
        self._client = OpenAI(
            base_url=self._settings.llm_base_url,
            api_key=self._settings.llm_api_key,
        )
        self._model = self._settings.llm_model
        self._max_tokens = self._settings.llm_max_tokens
        self._temperature = self._settings.llm_temperature

        logger.info(
            "LLM 客户端初始化完成: model=%s, base_url=%s",
            self._model,
            self._settings.llm_base_url,
        )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def generate(
        self,
        context: str,
        question: str,
        *,
        system_prompt: str | None = None,
        user_prompt_template: str | None = None,
    ) -> str:
        """同步调用 LLM 生成回答。

        Args:
            context: 检索到的代码上下文。
            question: 用户的问题。
            system_prompt: 自定义 System Prompt；为 ``None`` 时使用默认模板。
            user_prompt_template: 自定义 User Prompt 模板；为 ``None`` 时使用默认模板。

        Returns:
            LLM 生成的回答文本。

        Raises:
            RuntimeError: 当 API 调用失败时。
        """
        messages = self._build_messages(
            context,
            question,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
        )

        try:
            logger.info("开始同步调用 LLM: model=%s", self._model)
            response: ChatCompletion = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                stream=False,
            )

            answer = response.choices[0].message.content or ""
            logger.info(
                "LLM 调用完成: %d tokens (prompt=%d, completion=%d)",
                response.usage.total_tokens if response.usage else 0,
                response.usage.prompt_tokens if response.usage else 0,
                response.usage.completion_tokens if response.usage else 0,
            )
            return answer

        except Exception as exc:
            logger.error("LLM 调用失败: %s", exc)
            raise RuntimeError(f"LLM 调用失败: {exc}") from exc

    def generate_stream(
        self,
        context: str,
        question: str,
        *,
        system_prompt: str | None = None,
        user_prompt_template: str | None = None,
    ) -> Generator[StreamingChunk, None, None]:
        """流式调用 LLM 生成回答。

        Args:
            context: 检索到的代码上下文。
            question: 用户的问题。
            system_prompt: 自定义 System Prompt；为 ``None`` 时使用默认模板。
            user_prompt_template: 自定义 User Prompt 模板；为 ``None`` 时使用默认模板。

        Yields:
            :class:`StreamingChunk` 实例，包含本次 chunk 的内容和元数据。

        Raises:
            RuntimeError: 当 API 调用失败时。
        """
        messages = self._build_messages(
            context,
            question,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
        )

        try:
            logger.info("开始流式调用 LLM: model=%s", self._model)
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                stream=True,
            )

            chunk_index = 0
            for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason

                # 提取内容
                content = delta.content if delta and delta.content else ""

                if content or finish_reason:
                    yield StreamingChunk(
                        content=content,
                        finish_reason=finish_reason,
                        chunk_index=chunk_index,
                    )
                    chunk_index += 1

            logger.info("流式调用完成: 共 %d 个 chunk", chunk_index)

        except Exception as exc:
            logger.error("LLM 流式调用失败: %s", exc)
            raise RuntimeError(f"LLM 流式调用失败: {exc}") from exc

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        context: str,
        question: str,
        *,
        system_prompt: str | None = None,
        user_prompt_template: str | None = None,
    ) -> list[dict[str, str]]:
        """构建 LLM 消息列表。

        Args:
            context: 检索到的代码上下文。
            question: 用户的问题。
            system_prompt: 自定义 System Prompt。
            user_prompt_template: 自定义 User Prompt 模板。

        Returns:
            OpenAI 格式的消息列表。
        """
        # 使用默认模板或自定义模板
        sys_prompt = (system_prompt or SYSTEM_PROMPT).format(context=context)
        user_prompt = (user_prompt_template or USER_PROMPT_TEMPLATE).format(question=question)

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.debug(
            "消息构建完成: system_prompt=%d 字符, user_prompt=%d 字符",
            len(sys_prompt),
            len(user_prompt),
        )
        return messages

    def health_check(self) -> bool:
        """检查 LLM 服务是否可用。

        发送一个简单的测试请求，验证 API 连接是否正常。

        Returns:
            ``True`` 表示服务可用，``False`` 表示不可用。
        """
        try:
            self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
                stream=False,
            )
            logger.info("LLM 健康检查通过: model=%s", self._model)
            return True
        except Exception as exc:
            logger.warning("LLM 健康检查失败: %s", exc)
            return False
