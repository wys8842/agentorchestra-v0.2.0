"""Loop Agent实现 - 基于 Function Calling 的循环执行智能体"""

import asyncio
import json
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, Iterator, List, Optional, Tuple, cast

from ..core.agent import Agent
from ..core.config import Config
from ..core.lifecycle import EventType, LifecycleHook
from ..core.llm import SymphonyLLM
from ..core.message import Message
from ..core.streaming import StreamEvent, StreamEventType
from ..core.utils import duration_seconds, parse_tool_arguments, serialize_tool_calls

if TYPE_CHECKING:
    from agentorchestra.capability.tools.registry import ToolRegistry


class LoopAgent(Agent):
    """
    Loop Agent - 基于 Function Calling 的循环执行智能体

    这个 Agent 通过循环调用 LLM 配合工具，实现：
    1. 用户提问 → LLM 生成工具调用
    2. 执行工具 → 获取结果
    3. 将结果反馈给 LLM → 继续循环
    4. 直到 LLM 返回无工具调用（结束） 或 达到最大迭代次数

    适用场景：
    - 需要多轮工具交互的任务
    - 逻辑不确定、需要多次查询的问题
    - 替代简单的 ReAct/PlanSolve，提供更通用的循环机制

    """

    def __init__(
        self,
        name: str,
        llm: SymphonyLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        tool_registry: Optional['ToolRegistry'] = None,
        enable_tool_calling: bool = True,
        max_steps: int = 5
    ):
        """
        初始化 LoopAgent

        Args:
            name: Agent名称
            llm: LLM实例
            system_prompt: 系统提示词（可选）
            config: 配置对象
            tool_registry: 工具注册表（可选）
            enable_tool_calling: 是否启用工具调用（默认True）
            max_steps: 最大循环迭代次数（默认5）
        """
        # 传递 tool_registry 到基类
        super().__init__(
            name,
            llm,
            system_prompt,
            config,
            tool_registry=tool_registry
        )
        self.enable_tool_calling = enable_tool_calling and tool_registry is not None
        self.max_steps = max_steps

    def run(self, input_text: str, **kwargs) -> str:
        """
        运行 LoopAgent（基于 Function Calling 循环执行）

        Args:
            input_text: 用户输入
            **kwargs: 其他参数

        Returns:
            最终回复
        """
        from datetime import datetime

        session_start_time = datetime.now()

        # 构建消息列表
        messages = self._build_messages(input_text)

        # 记录用户消息
        if self.trace_logger:
            self.trace_logger.log_event(
                "session_start",
                {
                    "agent_name": self.name,
                    "agent_type": self.__class__.__name__,
                }
            )

        if self.trace_logger:
            self.trace_logger.log_event(
                "message_written",
                {"role": "user", "content": input_text}
            )

        # 如果没有启用工具调用，直接返回 LLM 响应
        if not self.enable_tool_calling or not self.tool_registry:
            llm_response = self.llm.invoke(messages, **kwargs)
            response_text: str = llm_response.content if (hasattr(llm_response, 'content') and llm_response.content is not None) else str(llm_response)

            # 保存到历史记录
            self.add_message(Message(input_text, "user"))
            self.add_message(Message(cast(str, response_text), "assistant"))

            if self.trace_logger:
                duration = duration_seconds(session_start_time)
                self.trace_logger.log_event(
                    "session_end",
                    {
                        "duration": duration,
                        "final_answer": response_text,
                        "status": "success",
                        "usage": llm_response.usage if hasattr(llm_response, 'usage') else {},
                        "latency_ms": llm_response.latency_ms if hasattr(llm_response, 'latency_ms') else 0
                    }
                )
                self.trace_logger.finalize()

            return response_text

        # 启用工具调用模式 - 循环执行
        tool_schemas = self._build_tool_schemas()

        current_iteration = 0
        final_response = ""

        while current_iteration < self.max_steps:  # type: ignore[operator]
            current_iteration += 1

            # 调用 LLM（Function Calling）
            try:
                response = self.llm.invoke_with_tools(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice="auto",
                    **kwargs
                )
            except Exception as e:
                if self.trace_logger:
                    self.trace_logger.log_event(
                        "error",
                        {"error_type": "LLM_ERROR", "message": str(e)},
                        step=current_iteration
                    )
                break

            # 获取响应消息
            response_message = response.choices[0].message

            # 记录模型输出
            if self.trace_logger:
                usage = response.usage
                self.trace_logger.log_event(
                    "model_output",
                    {
                        "content": response_message.content,
                        "tool_calls": len(response_message.tool_calls) if response_message.tool_calls else 0,
                        "usage": {
                            "prompt_tokens": usage.prompt_tokens if usage else 0,
                            "completion_tokens": usage.completion_tokens if usage else 0,
                            "total_tokens": usage.total_tokens if usage else 0
                        }
                    },
                    step=current_iteration
                )

            # 处理工具调用
            tool_calls = response_message.tool_calls
            if not tool_calls:
                # 没有工具调用，直接返回文本响应
                final_response = response_message.content or "抱歉，我无法回答这个问题。"
                break

            # 将助手消息添加到历史
            messages.append({
                "role": "assistant",
                "content": response_message.content,
                "tool_calls": serialize_tool_calls(tool_calls)
            })

            # 执行所有工具调用
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                tool_call_id = tool_call.id

                try:
                    arguments = parse_tool_arguments(tool_call)
                except json.JSONDecodeError as e:
                    if self.trace_logger:
                        self.trace_logger.log_event(
                            "tool_result",
                            {
                                "tool_name": tool_name,
                                "tool_call_id": tool_call_id,
                                "result": f"错误：参数格式不正确 - {str(e)}",
                                "status": "error"
                            },
                            step=current_iteration
                        )
                    continue

                # 执行工具并处理结果（使用基类公共方法，收敛 6 处重复逻辑）
                tool_result = self._execute_single_tool_call(
                    tool_name, arguments, tool_call_id, current_iteration)
                messages.append(tool_result)

        # 如果达到最大迭代次数，获取最后一次 LLM 回答
        if current_iteration >= self.max_steps and not final_response:  # type: ignore[operator]
            llm_response = self.llm.invoke(messages, **kwargs)
            final_response = cast(str, llm_response.content) if hasattr(llm_response, 'content') else str(llm_response)

        # 保存到历史记录
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_response, "assistant"))

        if self.trace_logger:
            duration = duration_seconds(session_start_time)
            self.trace_logger.log_event(
                "session_end",
                {
                    "duration": duration,
                    "total_steps": current_iteration,
                    "final_answer": final_response,
                    "status": "success" if current_iteration < self.max_steps else "max_steps_reached"  # type: ignore[operator]
                }
            )
            self.trace_logger.finalize()

        return final_response

    def _build_messages(self, input_text: str) -> List[Dict[str, Any]]:
        """构建消息列表"""
        messages = []

        # 添加系统提示词
        if self.system_prompt:
            messages.append({
                "role": "system",
                "content": self.system_prompt
            })

        # 添加历史消息
        for msg in self._history:
            if msg.role in ("user", "assistant", "system", "tool"):
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })

        # 融合多路上下文（GSSC，可选）
        gssc_context = self._build_context(input_text, history=self._history)
        user_content = gssc_context if gssc_context is not None else input_text

        # 添加用户问题
        messages.append({
            "role": "user",
            "content": user_content
        })

        return messages

    def _build_tool_schemas(self) -> List[Dict[str, Any]]:
        """构建工具 JSON Schema（复用基类方法 + 追加内置工具）"""
        schemas = []

        # 复用基类的 _build_tool_schemas() 获取用户工具
        if self.tool_registry:
            user_tool_schemas = super()._build_tool_schemas()
            schemas.extend(user_tool_schemas)

        return schemas

    async def _execute_tools_async(
        self,
        tool_calls: List[Any],
        current_iteration: int,
        on_tool_call: LifecycleHook = None
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """
        异步并行执行工具（经 registry 熔断保护）

        策略：用户工具并行执行（最多 max_concurrent_tools 个，走 registry.async_execute_tool）

        Args:
            tool_calls: 工具调用列表
            current_iteration: 当前迭代次数
            on_tool_call: 工具调用钩子

        Returns:
            [(tool_name, tool_call_id, result_dict), ...]
        """
        results: List[Tuple[str, str, Dict[str, Any]]] = []

        if not tool_calls:
            return results

        max_concurrent = getattr(self.config, 'max_concurrent_tools', 3)

        semaphore = asyncio.Semaphore(max_concurrent)

        async def execute_one(tc):
            async with semaphore:
                tool_name = tc.function.name
                tool_call_id = tc.id

                try:
                    arguments = parse_tool_arguments(tc)
                except json.JSONDecodeError as e:
                    return (tool_name, tool_call_id, {"content": f"错误：参数格式不正确 - {str(e)}"})

                # 触发工具调用事件
                await self._emit_event(
                    EventType.TOOL_CALL,
                    on_tool_call,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    args=arguments,
                    step=current_iteration
                )

                # 统一经 registry 异步执行（含熔断检查/观测埋点/结果记录）
                if self.tool_registry:
                    tool_response = await self.tool_registry.async_execute_tool(tool_name, arguments)
                    result_content = tool_response.text

                    # 截断保护（仅成功/部分结果）
                    if tool_response.status.value != "error":
                        try:
                            truncate_result = self.truncator.truncate(
                                tool_name=tool_name,
                                output=result_content
                            )
                            result_content = truncate_result.get('preview', result_content)
                        except Exception:
                            pass
                else:
                    result_content = self._execute_tool_call(tool_name, arguments)

                # 记录工具结果
                if self.trace_logger:
                    self.trace_logger.log_event(
                        "tool_result",
                        {
                            "tool_name": tool_name,
                            "tool_call_id": tool_call_id,
                            "result": result_content
                        },
                        step=current_iteration
                    )

                return (tool_name, tool_call_id, {"content": result_content})

        # 并行执行
        results = await asyncio.gather(*[execute_one(tc) for tc in tool_calls])

        return list(results)

    def add_tool(self, tool, auto_expand: bool = True) -> None:
        """
        添加工具到Agent（便利方法）

        .. deprecated::
            使用 `register_tool` 替代（与 ToolRegistry 命名一致）。

        Args:
            tool: Tool对象
            auto_expand: 是否自动展开可展开的工具（默认True）

        如果工具是可展开的（expandable=True），会自动展开为多个独立工具
        """
        import warnings
        warnings.warn(
            "Agent.add_tool is deprecated, use register_tool instead",
            DeprecationWarning,
            stacklevel=2,
        )
        if not self.tool_registry:
            from agentorchestra.capability.tools.registry import ToolRegistry
            self.tool_registry = ToolRegistry()
            self.enable_tool_calling = True

        # 直接使用 ToolRegistry 的 register_tool 方法
        # ToolRegistry 会自动处理工具展开
        self.tool_registry.register_tool(tool, auto_expand=auto_expand)

    def remove_tool(self, tool_name: str) -> bool:
        """移除工具（便利方法）

        .. deprecated::
            使用 `unregister_tool` 替代。
        """
        import warnings
        warnings.warn(
            "Agent.remove_tool is deprecated, use unregister_tool instead",
            DeprecationWarning,
            stacklevel=2,
        )
        if self.tool_registry:
            return self.tool_registry.unregister(tool_name)
        return False

    def register_tool(self, tool, auto_expand: bool = True) -> None:
        """注册工具到Agent（与 ToolRegistry.register_tool 命名一致）"""
        if not self.tool_registry:
            from agentorchestra.capability.tools.registry import ToolRegistry
            self.tool_registry = ToolRegistry()
            self.enable_tool_calling = True
        self.tool_registry.register_tool(tool, auto_expand=auto_expand)

    def unregister_tool(self, tool_name: str) -> bool:
        """取消注册工具（与 ToolRegistry.unregister 命名一致）"""
        if self.tool_registry:
            return self.tool_registry.unregister(tool_name)
        return False

    def list_tools(self) -> list:
        """列出所有可用工具"""
        if self.tool_registry:
            return self.tool_registry.list_tools()
        return []

    def has_tools(self) -> bool:
        """检查是否有可用工具"""
        return self.enable_tool_calling and self.tool_registry is not None

    # ==================== 流式执行 ====================

    def stream_run(self, input_text: str, **kwargs) -> Iterator[str]:
        """
        流式运行 Agent

        Args:
            input_text: 用户输入
            **kwargs: 其他参数

        Yields:
            Agent 响应片段
        """
        # 构建消息列表
        messages = []

        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        for msg in self._history:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": input_text})

        # 流式调用 LLM
        full_response = ""
        for chunk in self.llm.stream_invoke(messages, **kwargs):
            full_response += chunk
            yield chunk

        # 保存完整对话到历史记录
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(full_response, "assistant"))

    async def arun_stream(  # type: ignore[override]
        self,
        input_text: str,
        on_start: LifecycleHook = None,
        on_step: LifecycleHook = None,
        on_tool_call: LifecycleHook = None,
        on_finish: LifecycleHook = None,
        on_error: LifecycleHook = None,
        **kwargs
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        LoopAgent 真正的流式执行

        实时返回：
        - LLM 输出的每个文本块
        - 工具调用的开始和结束
        - 步骤的开始和结束

        Args:
            input_text: 用户输入
            on_start: 开始钩子
            on_step: 步骤钩子
            on_tool_call: 工具调用钩子
            on_finish: 完成钩子
            on_error: 错误钩子
            **kwargs: 其他参数

        Yields:
            StreamEvent: 流式事件
        """
        # 发送开始事件
        yield StreamEvent.create(
            StreamEventType.AGENT_START,
            self.name,
            input_text=input_text
        )

        await self._emit_event(EventType.AGENT_START, on_start, input_text=input_text)

        try:
            # 构建消息列表
            messages: List[Dict[str, Any]] = []

            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})

            for msg in self._history:
                messages.append({"role": msg.role, "content": msg.content})

            messages.append({"role": "user", "content": input_text})

            # LLM 流式调用 + 工具循环
            full_response = ""
            current_iteration = 0

            while current_iteration < self.max_steps:  # type: ignore[operator]
                current_iteration += 1

                # 发送步骤开始事件
                yield StreamEvent.create(
                    StreamEventType.STEP_START,
                    self.name,
                    iteration=current_iteration,
                    max_steps=self.max_steps
                )

                await self._emit_event(EventType.STEP_START, on_step, step=current_iteration)

                # LLM 流式调用（单轮）
                try:
                    async for chunk in self.llm.astream_invoke(messages, **kwargs):
                        full_response += chunk

                        # 发送 LLM 输出块
                        yield StreamEvent.create(
                            StreamEventType.LLM_CHUNK,
                            self.name,
                            chunk=chunk,
                            iteration=current_iteration
                        )

                except Exception as e:
                    error_msg = f"LLM 调用失败: {str(e)}"
                    yield StreamEvent.create(
                        StreamEventType.ERROR,
                        self.name,
                        error=error_msg,
                        iteration=current_iteration
                    )
                    await self._emit_event(EventType.AGENT_ERROR, on_error, error=error_msg)
                    raise

                # 解析工具调用（需要完整响应）
                # 使用异步调用获取 tool_calls
                try:
                    response = await self.llm.ainvoke_with_tools(
                        messages=messages,
                        tools=self._build_tool_schemas(),
                        tool_choice="auto",
                        **kwargs
                    )

                    response_message = response.choices[0].message
                    tool_calls = response_message.tool_calls

                    if not tool_calls:
                        # 没有工具调用，结束循环
                        final_answer = full_response or "抱歉，我无法回答这个问题。"
                        yield StreamEvent.create(
                            StreamEventType.AGENT_FINISH,
                            self.name,
                            result=final_answer,
                            total_steps=current_iteration,
                            max_steps_reached=False
                        )
                        await self._emit_event(EventType.AGENT_FINISH, on_finish, result=final_answer)
                        self.add_message(Message(input_text, "user"))
                        self.add_message(Message(final_answer, "assistant"))
                        return

                    # 添加助手消息到历史
                    messages.append({
                        "role": "assistant",
                        "content": response_message.content,
                        "tool_calls": serialize_tool_calls(tool_calls)
                    })

                    # 异步并行执行工具调用
                    tool_results = await self._execute_tools_async(
                        tool_calls,
                        current_iteration,
                        on_tool_call
                    )

                    # 发送工具结果事件并添加到消息
                    for tool_name, tool_call_id, result_dict in tool_results:
                        yield StreamEvent.create(
                            StreamEventType.TOOL_CALL_FINISH,
                            self.name,
                            tool_name=tool_name,
                            tool_call_id=tool_call_id,
                            result=result_dict["content"],
                            iteration=current_iteration
                        )

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result_dict["content"]
                        })

                    # 发送步骤完成事件
                    yield StreamEvent.create(
                        StreamEventType.STEP_FINISH,
                        self.name,
                        iteration=current_iteration
                    )

                    await self._emit_event(
                        EventType.STEP_FINISH,
                        on_step,
                        step=current_iteration,
                        tool_calls=len(tool_calls)
                    )

                except Exception as e:
                    error_msg = f"工具执行失败: {str(e)}"
                    yield StreamEvent.create(
                        StreamEventType.ERROR,
                        self.name,
                        error=error_msg,
                        iteration=current_iteration
                    )
                    await self._emit_event(EventType.AGENT_ERROR, on_error, error=error_msg)
                    raise

            # 达到最大迭代次数
            yield StreamEvent.create(
                StreamEventType.AGENT_FINISH,
                self.name,
                result=full_response,
                total_steps=current_iteration,
                max_steps_reached=True
            )
            await self._emit_event(
                EventType.AGENT_FINISH,
                on_finish,
                result=full_response,
                status="timeout"
            )
            self.add_message(Message(input_text, "user"))
            self.add_message(Message(full_response, "assistant"))

        except Exception as e:
            error_msg = f"Agent 执行失败: {str(e)}"
            yield StreamEvent.create(
                StreamEventType.ERROR,
                self.name,
                error=error_msg,
                error_type=type(e).__name__
            )
            await self._emit_event(EventType.AGENT_ERROR, on_error, error=error_msg)
            raise
