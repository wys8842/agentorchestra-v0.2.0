"""Symphony统一LLM接口 - 支持OpenAI、Anthropic、Gemini等多种接口"""

import asyncio
import os
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Union

from .exceptions import SymphonyException
from .llm_adapters import BaseLLMAdapter, create_adapter
from .llm_response import LLMResponse


class SymphonyLLM:
    """
    Symphony统一LLM客户端

    设计理念：
    - 统一配置：只需 LLM_MODEL_ID、LLM_API_KEY、LLM_BASE_URL、LLM_TIMEOUT
    - 自动适配：根据base_url自动选择适配器（OpenAI/Anthropic/Gemini）
    - 统计信息：返回token使用量、耗时等信息，方便日志记录
    - Thinking Model：自动识别并处理推理过程（o1、deepseek-reasoner等）

    支持的接口：
    - OpenAI及所有兼容接口（DeepSeek、Qwen、Kimi、智谱、Ollama等）
    - Anthropic Claude
    - Google Gemini
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        quota_manager: Optional[Any] = None,     # M6: QuotaManager（可选）
        usage_recorder: Optional[Any] = None,    # M6: UsageRecorder（可选）
        **kwargs
    ):
        """
        初始化LLM客户端

        参数优先级：传入参数 > 环境变量

        Args:
            model: 模型名称，默认从 LLM_MODEL_ID 读取
            api_key: API密钥，默认从 LLM_API_KEY 读取
            base_url: 服务地址，默认从 LLM_BASE_URL 读取
            temperature: 温度参数，默认0.7
            max_tokens: 最大token数
            timeout: 超时时间（秒），默认从 LLM_TIMEOUT 读取，默认60秒
            max_retries: 调用失败最大重试次数（默认3，0=不重试）
            retry_base_delay: 重试基础退避延迟（秒，默认1）
        """
        # 加载配置
        self.model = model or os.getenv("LLM_MODEL_ID")
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL")
        self.timeout = timeout or int(os.getenv("LLM_TIMEOUT", "60"))

        self.temperature = temperature
        self.max_tokens = max_tokens
        self.kwargs = kwargs

        # 重试机制
        from .retry import RetryManager
        self.retry_manager = RetryManager(
            max_retries=max_retries,
            base_delay=retry_base_delay,
        )

        # 验证必要参数
        if not self.model:
            raise SymphonyException("必须提供模型名称（model参数或LLM_MODEL_ID环境变量）")
        if not self.api_key:
            raise SymphonyException("必须提供API密钥（api_key参数或LLM_API_KEY环境变量）")
        if not self.base_url:
            raise SymphonyException("必须提供服务地址（base_url参数或LLM_BASE_URL环境变量）")

        # 确保类型正确（通过验证后不可能为 None）
        assert self.model is not None
        assert self.api_key is not None
        assert self.base_url is not None

        # 创建适配器（自动检测）
        self._adapter: BaseLLMAdapter = create_adapter(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            model=self.model
        )

        # 最后一次调用的统计信息（用于流式调用）
        self.last_call_stats: Optional[LLMResponse] = None

        # 可观测性：日志 + 指标
        from .logging import get_logger
        from .metrics import get_metrics
        from .tracing import get_tracer
        self.logger = get_logger("core.llm")
        self.metrics = get_metrics()
        self.tracer = get_tracer()
        self.provider = self.base_url.split("//")[-1].split(".")[0] if "//" in self.base_url else "custom"

        # M6：多租户配额与用量记录（可选；无 tenant context 不计数）
        self.quota_manager = quota_manager
        self.usage_recorder = usage_recorder

    # ==================== M6 配额/用量辅助 ====================

    def _charge_and_record(self, tokens: int, latency_ms: float) -> None:
        """配额扣减 + 用量记录（仅当有 quota/recorder 且 tenant context 存在）。"""
        from agentorchestra.governance.tenancy.tenant import TenantManager

        tenant_id = TenantManager.tenant_id()
        if tenant_id is None:
            return  # 无租户上下文：不计费（向后兼容）

        if self.quota_manager is not None:
            self.quota_manager.charge(tenant_id, tokens)

        if self.usage_recorder is not None:
            self.usage_recorder.record(
                tenant_id=tenant_id, model=self.model or "",
                tokens=tokens, latency_ms=latency_ms)

    def think(self, messages: List[Dict[str, str]], temperature: Optional[float] = None) -> Iterator[str]:
        """
        调用大语言模型进行思考，并返回流式响应。
        这是主要的调用方法，默认使用流式响应以获得更好的用户体验。

        Args:
            messages: 消息列表
            temperature: 温度参数，如果未提供则使用初始化时的值

        Yields:
            str: 流式响应的文本片段

        Note:
            流式调用结束后，可通过 llm.last_call_stats 获取统计信息
        """
        self.logger.info(f"正在调用 {self.model} 模型...")

        # 准备参数
        kwargs = {
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens

        try:
            for chunk in self._adapter.stream_invoke(messages, **kwargs):
                self.logger.debug(f"LLM chunk: {chunk}")
                yield chunk
            self.logger.info("大语言模型响应成功")

            # 保存统计信息
            if hasattr(self._adapter, 'last_stats'):
                self.last_call_stats = self._adapter.last_stats

        except Exception as e:
            self.logger.error(f"调用LLM API时发生错误: {e}")
            raise

    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> LLMResponse:
        """
        非流式调用LLM，返回完整响应对象。

        Args:
            messages: 消息列表
            **kwargs: 其他参数（temperature, max_tokens等）

        Returns:
            LLMResponse: 包含内容、统计信息、推理过程（thinking model）的响应对象

        Example:
            response = llm.invoke([{"role": "user", "content": "你好"}])
            print(response.content)  # 回复内容
            print(response.usage)    # token使用量
            print(response.latency_ms)  # 耗时
            if response.reasoning_content:  # thinking model的推理过程
                print(response.reasoning_content)
        """
        # 合并参数
        call_kwargs = {
            "temperature": kwargs.pop("temperature", self.temperature),
        }
        if self.max_tokens:
            call_kwargs["max_tokens"] = kwargs.pop("max_tokens", self.max_tokens)
        call_kwargs.update(kwargs)

        # 带重试调用 + 观测埋点（追踪 + 指标 + 日志）
        import time
        start = time.monotonic()
        with self.tracer.span("llm.invoke", {"model": self.model}) as span:
            try:
                response = self.retry_manager.execute(
                    self._adapter.invoke, messages, **call_kwargs)
            except Exception as e:
                span.set_error()
                span.set_attribute("error", str(e))
                self.metrics.record_llm_call(self.model, self.provider, 0, 0)  # type: ignore[arg-type]
                self.logger.exception("LLM 调用失败", extra={"model": self.model})
                raise

            latency_ms = (time.monotonic() - start) * 1000
            tokens = 0
            if hasattr(response, 'usage') and response.usage:
                tokens = response.usage.get("total_tokens", 0)
            span.set_attribute("tokens", tokens)
            span.set_attribute("duration_ms", round(latency_ms, 1))
            self.metrics.record_llm_call(self.model, self.provider, tokens, latency_ms)  # type: ignore[arg-type]
            self.logger.info("LLM 调用完成", extra={
                "model": self.model, "duration_ms": round(latency_ms, 1),
                "tokens": tokens})
            # M6：多租户配额 + 用量（成功返回前；超限抛 QuotaExceeded）
            try:
                self._charge_and_record(tokens, latency_ms)
            except Exception:
                raise
            return response

    def stream_invoke(self, messages: List[Dict[str, str]], **kwargs) -> Iterator[str]:
        """
        流式调用LLM的别名方法，与think方法功能相同。
        保持向后兼容性。

        Args:
            messages: 消息列表
            **kwargs: 其他参数

        Yields:
            str: 流式响应的文本片段

        Note:
            流式调用结束后，可通过 llm.last_call_stats 获取统计信息
        """
        temperature = kwargs.pop("temperature", self.temperature)

        # 准备参数
        call_kwargs = {}
        if self.max_tokens:
            call_kwargs["max_tokens"] = kwargs.pop("max_tokens", self.max_tokens)
        call_kwargs.update(kwargs)

        for chunk in self._adapter.stream_invoke(messages, temperature=temperature, **call_kwargs):
            yield chunk

        # 保存统计信息
        if hasattr(self._adapter, 'last_stats'):
            self.last_call_stats = self._adapter.last_stats

    def invoke_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        tool_choice: Union[str, Dict] = "auto",
        **kwargs
    ) -> Any:
        """
        调用 LLM 并支持工具调用（Function Calling）

        这是支持 OpenAI Function Calling 的核心方法，用于结构化工具调用。

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            tools: 工具 schema 列表，格式为 OpenAI Function Calling 规范
            tool_choice: 工具选择策略
                - "auto": 让模型自动决定是否调用工具（默认）
                - "none": 强制不调用工具
                - "required": 强制调用工具
                - {"type": "function", "function": {"name": "tool_name"}}: 强制调用指定工具
            **kwargs: 其他参数（temperature, max_tokens 等）

        Returns:
            原生响应对象，包含 tool_calls 信息

        Raises:
            SymphonyException: 当 LLM 调用失败时
        """
        # 合并参数
        call_kwargs = {
            "temperature": kwargs.pop("temperature", self.temperature),
            "tool_choice": tool_choice,
        }
        if self.max_tokens:
            call_kwargs["max_tokens"] = kwargs.pop("max_tokens", self.max_tokens)
        call_kwargs.update(kwargs)

        # 带重试调用 + 观测埋点（追踪 + 指标 + 日志）
        import time
        start = time.monotonic()
        with self.tracer.span("llm.invoke_with_tools", {"model": self.model}) as span:
            try:
                response = self.retry_manager.execute(
                    self._adapter.invoke_with_tools, messages, tools, **call_kwargs)
            except Exception as e:
                span.set_error()
                span.set_attribute("error", str(e))
                self.metrics.record_llm_call(self.model, self.provider, 0, 0)  # type: ignore[arg-type]
                self.logger.exception("LLM 工具调用失败", extra={"model": self.model})
                raise

            latency_ms = (time.monotonic() - start) * 1000
            tokens = 0
            try:
                if hasattr(response, "usage") and response.usage:
                    tokens = getattr(response.usage, "total_tokens", 0) or 0
            except Exception:
                pass
            span.set_attribute("tokens", tokens)
            span.set_attribute("duration_ms", round(latency_ms, 1))
            self.metrics.record_llm_call(self.model, self.provider, tokens, latency_ms)  # type: ignore[arg-type]
            self.logger.info("LLM 工具调用完成", extra={
                "model": self.model, "duration_ms": round(latency_ms, 1),
                "tokens": tokens})
            return response

    # ==================== 异步方法 ====================

    async def ainvoke(self, messages: List[Dict[str, str]], **kwargs) -> LLMResponse:
        """
        异步非流式调用 LLM

        在线程池中运行同步 invoke 方法，避免阻塞事件循环

        Args:
            messages: 消息列表
            **kwargs: 其他参数（temperature, max_tokens等）

        Returns:
            LLMResponse: 包含内容、统计信息的响应对象

        Example:
            response = await llm.ainvoke([{"role": "user", "content": "你好"}])
            print(response.content)
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.invoke(messages, **kwargs)
        )

    async def astream_invoke(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> AsyncIterator[str]:
        """
        真正的异步流式调用 LLM（使用 adapter 的异步实现）

        Args:
            messages: 消息列表
            **kwargs: 其他参数

        Yields:
            str: 流式响应的文本片段（实时返回）

        Example:
            async for chunk in llm.astream_invoke(messages):
                print(chunk, end="", flush=True)
        """
        # 使用 adapter 的异步流式方法
        async for chunk in self._adapter.astream_invoke(messages, **kwargs):
            yield chunk

        # 保存统计信息
        if hasattr(self._adapter, 'last_stats'):
            self.last_call_stats = self._adapter.last_stats

    async def ainvoke_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        tool_choice: Union[str, Dict] = "auto",
        **kwargs
    ) -> Any:
        """
        异步调用 LLM 并支持工具调用（Function Calling）

        Args:
            messages: 消息列表
            tools: 工具 schema 列表
            tool_choice: 工具选择策略
            **kwargs: 其他参数

        Returns:
            原生响应对象，包含 tool_calls 信息
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.invoke_with_tools(messages, tools, tool_choice, **kwargs)
        )
