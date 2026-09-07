"""ReAct Agent - 基于 Function Calling 的实现"""

import asyncio
import json
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from agentorchestra.capability.tools.registry import ToolRegistry
from agentorchestra.runtime.core.agent import Agent
from agentorchestra.runtime.core.agent.lifecycle import EventType, LifecycleHook
from agentorchestra.runtime.core.config import Config
from agentorchestra.runtime.core.llm import SymphonyLLM
from agentorchestra.runtime.core.llm.streaming import StreamEvent, StreamEventType
from agentorchestra.runtime.core.message import Message
from agentorchestra.runtime.core.utils import (
    duration_seconds,
    parse_tool_arguments,
    serialize_tool_calls,
)

DEFAULT_REACT_SYSTEM_PROMPT = """你是一个具备推理和行动能力的 AI 助手。

## 工具调用流程
1. **Thought**：记录推理过程（参数：reasoning）
2. **业务工具**：获取信息或执行操作（可多次调用）
3. **Finish**：返回最终答案（参数：answer）

## 提醒
- 主动使用 Thought 记录推理
- 只有在确信有足够信息时才调用 Finish
"""


class ReActAgent(Agent):
    """ReAct（推理-行动）循环 Agent，基于 Function Calling 执行

    内置 Thought（记录推理）与 Finish（返回最终答案）两种内置工具，
    支持同步 run、异步 arun 与流式 arun_stream 三种执行方式。
    """

    def __init__(
        self,
        name: str,
        llm: SymphonyLLM,
        tool_registry: Optional[ToolRegistry] = None,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 5
    ):
        """
        初始化 ReActAgent

        Args:
            name: Agent 名称
            llm: LLM 实例
            tool_registry: 工具注册表（可选）
            system_prompt: 系统提示词（可选，默认使用 DEFAULT_REACT_SYSTEM_PROMPT）
            config: 配置对象
            max_steps: 最大执行步数
        """
        # 传递 tool_registry 到基类
        super().__init__(
            name,
            llm,
            system_prompt or DEFAULT_REACT_SYSTEM_PROMPT,
            config,
            tool_registry=tool_registry or ToolRegistry()
        )

        self.max_steps = max_steps

        # 内置工具标记（用于特殊处理）
        self._builtin_tools = {"Thought", "Finish"}

    def add_tool(self, tool):
        """添加工具到工具注册表

        .. deprecated::
            使用 `register_tool` 替代（与 ToolRegistry 命名一致）。
        """
        import warnings
        warnings.warn(
            "Agent.add_tool is deprecated, use register_tool instead",
            DeprecationWarning,
            stacklevel=2,
        )
        self.tool_registry.register_tool(tool)

    def register_tool(self, tool):
        """注册工具到工具注册表"""
        self.tool_registry.register_tool(tool)

    def run(self, input_text: str, **kwargs) -> str:
        """
        运行 ReAct Agent

        Args:
            input_text: 用户问题
            **kwargs: 其他参数

        Returns:
            最终答案
        """
        session_start_time = datetime.now()

        try:
            # 执行主逻辑
            final_answer = self._run_impl(input_text, session_start_time, **kwargs)

            # 更新元数据
            self._session_metadata["total_steps"] = getattr(self, '_current_step', 0)
            self._session_metadata["total_tokens"] = getattr(self, '_total_tokens', 0)

            return final_answer

        except KeyboardInterrupt:
            # Ctrl+C 时自动保存
            self.logger.info("用户中断，自动保存会话...")
            if self.session_store:
                try:
                    filepath = self.save_session("session-interrupted")
                    self.logger.info("会话已保存: %s", filepath)
                except Exception as e:
                    self.logger.warning("保存失败: %s", e)
            raise

        except Exception as e:
            # 错误时也尝试保存
            self.logger.warning("发生错误: %s", e)
            if self.session_store:
                try:
                    filepath = self.save_session("session-error")
                    self.logger.info("会话已保存: %s", filepath)
                except Exception as save_error:
                    self.logger.warning("保存失败: %s", save_error)
            raise

    def _run_impl(self, input_text: str, session_start_time, **kwargs) -> str:
        """
        ReAct Agent 主逻辑实现

        Args:
            input_text: 用户问题
            session_start_time: 会话开始时间
            **kwargs: 其他参数

        Returns:
            最终答案
        """
        # 构建消息列表
        messages = self._build_messages(input_text)

        # 构建工具 schemas（包含内置工具和用户工具）
        tool_schemas = self._build_tool_schemas()

        current_step: int = 0
        total_tokens = 0

        # 记录用户消息
        if self.trace_logger:
            self.trace_logger.log_event(
                "message_written",
                {"role": "user", "content": input_text}
            )

        print(f"\n🤖 {self.name} 开始处理问题: {input_text}")

        while current_step < self.max_steps:  # type: ignore[operator]
            current_step += 1
            print(f"\n--- 第 {current_step} 步 ---")

            # 保存当前步数（用于异常时保存）
            self._current_step = current_step

            # 调用 LLM（Function Calling）
            try:
                response = self.llm.invoke_with_tools(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice="auto",
                    **kwargs
                )
            except Exception as e:
                print(f"❌ LLM 调用失败: {e}")
                if self.trace_logger:
                    self.trace_logger.log_event(
                        "error",
                        {"error_type": "LLM_ERROR", "message": str(e)},
                        step=current_step
                    )
                break

            # 获取响应消息
            response_message = response.choices[0].message

            # 累计 tokens
            if response.usage:
                total_tokens += response.usage.total_tokens
                self._total_tokens = total_tokens

            # 记录模型输出
            self._log_model_output(response_message, current_step, response.usage)

            # 处理工具调用
            tool_calls = response_message.tool_calls
            if not tool_calls:
                # 没有工具调用，直接返回文本响应
                final_answer = response_message.content or "抱歉，我无法回答这个问题。"
                print(f"💬 直接回复: {final_answer}")

                return self._save_and_finalize(input_text, final_answer, session_start_time, current_step)

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
                    print(f"❌ 工具参数解析失败: {e}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": f"错误：参数格式不正确 - {str(e)}"
                    })
                    continue

                # 记录工具调用
                if self.trace_logger:
                    self.trace_logger.log_event(
                        "tool_call",
                        {
                            "tool_name": tool_name,
                            "tool_call_id": tool_call_id,
                            "args": arguments
                        },
                        step=current_step
                    )

                # 检查是否是内置工具
                if tool_name in self._builtin_tools:
                    result = self._handle_builtin_tool(tool_name, arguments)
                    print(f"🔧 {tool_name}: {result['content']}")

                    # 记录工具结果
                    if self.trace_logger:
                        self.trace_logger.log_event(
                            "tool_result",
                            {
                                "tool_name": tool_name,
                                "tool_call_id": tool_call_id,
                                "status": "success",
                                "result": result['content']
                            },
                            step=current_step
                        )

                    # 检查是否是 Finish
                    if tool_name == "Finish" and result.get("finished"):
                        final_answer = result["final_answer"]
                        print(f"🎉 最终答案: {final_answer}")

                        return self._save_and_finalize(input_text, final_answer,
                                                       session_start_time, current_step)

                    # 添加工具结果到消息
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result['content']
                    })
                else:
                    # 用户工具
                    print(f"🎬 调用工具: {tool_name}({arguments})")

                    # 执行工具并处理结果（使用基类公共方法，收敛 6 处重复逻辑）
                    tool_result = self._execute_single_tool_call(
                        tool_name, arguments, tool_call_id, current_step)

                    result = tool_result["content"]
                    messages.append(tool_result)

                    # 检查是否是错误
                    if result.startswith("❌"):
                        print(result)
                    else:
                        print(f"👀 观察: {result}")

        # 达到最大步数
        print("⏰ 已达到最大步数，流程终止。")
        final_answer = "抱歉，我无法在限定步数内完成这个任务。"

        return self._save_and_finalize(input_text, final_answer, session_start_time,
                                       current_step, status="timeout")

    def _build_messages(self, input_text: str) -> List[Dict[str, Any]]:
        """构建消息列表"""
        messages = []

        # 添加系统提示词
        if self.system_prompt:
            messages.append({
                "role": "system",
                "content": self.system_prompt
            })

        # 融合多路上下文（GSSC，可选）
        gssc_context = self._build_context(input_text)
        user_content = gssc_context if gssc_context is not None else input_text

        # 添加用户问题
        messages.append({
            "role": "user",
            "content": user_content
        })

        return messages

    def _build_tool_schemas(self) -> List[Dict[str, Any]]:
        """构建工具 JSON Schema（包含内置工具和用户工具）

        复用基类的 _build_tool_schemas()，并追加 ReAct 内置工具
        """
        schemas = []

        # 1. 添加内置工具：Thought
        schemas.append({
            "type": "function",
            "function": {
                "name": "Thought",
                "description": "分析问题，制定策略，记录推理过程。在需要思考时调用此工具。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reasoning": {
                            "type": "string",
                            "description": "你的推理过程和分析"
                        }
                    },
                    "required": ["reasoning"]
                }
            }
        })

        # 2. 添加内置工具：Finish
        schemas.append({
            "type": "function",
            "function": {
                "name": "Finish",
                "description": "当你有足够信息得出结论时，使用此工具返回最终答案。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {
                       "type": "string",
                            "description": "最终答案"
                        }
                    },
                    "required": ["answer"]
                }
            }
        })

        # 3. 添加用户工具（复用基类方法）
        if self.tool_registry:
            user_tool_schemas = super()._build_tool_schemas()
            schemas.extend(user_tool_schemas)

        return schemas

    def _handle_builtin_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """处理内置工具调用"""
        if tool_name == "Thought":
            reasoning = arguments.get("reasoning", "")
            return {
                "content": f"推理: {reasoning}",
                "finished": False
            }
        elif tool_name == "Finish":
            answer = arguments.get("answer", "")
            return {
                "content": f"最终答案: {answer}",
                "finished": True,
                "final_answer": answer
            }
        else:
            return {
                "content": f"未知的内置工具: {tool_name}",
                "finished": False
            }

    # ==================== 公共辅助方法（三套循环复用） ====================

    def _log_model_output(self, response_message, current_step: int, usage=None):
        """记录模型输出到 trace

        Args:
            response_message: LLM 返回消息对象
            current_step: 当前步骤
            usage: LLM 响应级 usage 对象（优先使用）
        """
        if not self.trace_logger:
            return
        u = usage if usage is not None else getattr(response_message, 'usage', None)
        self.trace_logger.log_event(
            "model_output",
            {
                "content": response_message.content or "",
                "tool_calls": len(response_message.tool_calls) if response_message.tool_calls else 0,
                "usage": {
                    "total_tokens": u.total_tokens if u is not None else 0,
                    "cost": 0.0
                }
            },
            step=current_step
        )

    def _finalize_session(self, session_start_time, current_step: int,
                          final_answer: str, status: str):
        """统一会话结束收尾：记录 session_end + finalize trace"""
        if self.trace_logger:
            duration = duration_seconds(session_start_time)
            self.trace_logger.log_event(
                "session_end",
                {
                    "duration": duration,
                    "total_steps": current_step,
                    "final_answer": final_answer,
                    "status": status
                }
            )
            self.trace_logger.finalize()

    def _save_and_finalize(self, input_text: str, final_answer: str,
                           session_start_time, current_step: int,
                           status: str = "success") -> str:
        """保存历史 + 会话结束收尾，返回最终答案"""
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_answer, "assistant"))
        self._finalize_session(session_start_time, current_step, final_answer, status)
        return final_answer

    # ==================== 异步方法 ====================

    async def arun(  # type: ignore[override]
        self,
        input_text: str,
        on_start: LifecycleHook = None,
        on_step: LifecycleHook = None,
        on_tool_call: LifecycleHook = None,
        on_finish: LifecycleHook = None,
        on_error: LifecycleHook = None,
        **kwargs
    ) -> str:
        """
        异步执行 ReAct Agent（完整版本）

        支持：
        - 工具并行执行（独立工具）
        - 生命周期钩子
        - 异步 LLM 调用

        Args:
            input_text: 用户问题
            on_start: Agent 开始执行时的钩子
            on_step: 每个推理步骤的钩子
            on_tool_call: 工具调用时的钩子
            on_finish: Agent 执行完成时的钩子
            on_error: 发生错误时的钩子
            **kwargs: 其他参数

        Returns:
            最终答案
        """
        session_start_time = datetime.now()

        # 触发开始事件
        await self._emit_event(
            EventType.AGENT_START,
            on_start,
            input_text=input_text
        )

        try:
            # 构建消息列表
            messages = self._build_messages(input_text)
            tool_schemas = self._build_tool_schemas()

            current_step: int = 0
            total_tokens = 0

            # 记录用户消息
            if self.trace_logger:
                self.trace_logger.log_event(
                    "message_written",
                    {"role": "user", "content": input_text}
                )

            print(f"\n🤖 {self.name} 开始处理问题: {input_text}")

            while current_step < self.max_steps:  # type: ignore[operator]
                current_step += 1
                self._current_step = current_step
                print(f"\n--- 第 {current_step} 步 ---")

                # 触发步骤开始事件
                await self._emit_event(
                    EventType.STEP_START,
                    on_step,
                    step=current_step
                )

                # 异步调用 LLM
                try:
                    response = await self.llm.ainvoke_with_tools(
                        messages=messages,
                        tools=tool_schemas,
                        tool_choice="auto",
                        **kwargs
                    )
                except Exception as e:
                    print(f"❌ LLM 调用失败: {e}")
                    await self._emit_event(
                        EventType.AGENT_ERROR,
                        on_error,
                        error=str(e),
                        step=current_step
                    )
                    break

                # 获取响应消息
                response_message = response.choices[0].message

                # 累计 tokens
                if response.usage:
                    total_tokens += response.usage.total_tokens
                    self._total_tokens = total_tokens

                # 记录模型输出
                self._log_model_output(response_message, current_step, response.usage)

                # 处理工具调用
                tool_calls = response_message.tool_calls
                if not tool_calls:
                    # 没有工具调用，直接返回
                    final_answer = response_message.content or "抱歉，我无法回答这个问题。"
                    print(f"💬 直接回复: {final_answer}")

                    self.add_message(Message(input_text, "user"))
                    self.add_message(Message(final_answer, "assistant"))

                    await self._emit_event(
                        EventType.AGENT_FINISH,
                        on_finish,
                        result=final_answer,
                        total_steps=current_step,
                        total_tokens=total_tokens
                    )

                    self._finalize_session(session_start_time, current_step, final_answer, "success")

                    return final_answer

                # 将助手消息添加到历史
                messages.append({
                    "role": "assistant",
                    "content": response_message.content,
                    "tool_calls": serialize_tool_calls(tool_calls)
                })

                # 异步并行执行工具
                tool_results = await self._execute_tools_async(
                    tool_calls,
                    current_step,
                    on_tool_call
                )

                # 检查是否有 Finish 工具
                for tool_name, tool_call_id, result in tool_results:
                    if tool_name == "Finish" and result.get("finished"):
                        final_answer = result["final_answer"]
                        print(f"🎉 最终答案: {final_answer}")

                        self.add_message(Message(input_text, "user"))
                        self.add_message(Message(final_answer, "assistant"))

                        await self._emit_event(
                            EventType.AGENT_FINISH,
                            on_finish,
                            result=final_answer,
                            total_steps=current_step,
                            total_tokens=total_tokens
                        )

                        self._finalize_session(session_start_time, current_step, final_answer, "success")

                        return final_answer

                    # 添加工具结果到消息
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result.get('content', str(result))
                    })

                # 触发步骤完成事件
                await self._emit_event(
                    EventType.STEP_FINISH,
                    on_step,
                    step=current_step,
                    tool_calls=len(tool_calls)
                )

            # 达到最大步数
            print("⏰ 已达到最大步数，流程终止。")
            final_answer = "抱歉，我无法在限定步数内完成这个任务。"

            self.add_message(Message(input_text, "user"))
            self.add_message(Message(final_answer, "assistant"))

            await self._emit_event(
                EventType.AGENT_FINISH,
                on_finish,
                result=final_answer,
                total_steps=current_step,
                total_tokens=total_tokens,
                status="timeout"
            )

            self._finalize_session(session_start_time, current_step, final_answer, "timeout")

            return final_answer

        except Exception as e:
            await self._emit_event(
                EventType.AGENT_ERROR,
                on_error,
                error=str(e),
                error_type=type(e).__name__
            )
            raise

    async def _execute_tools_async(
        self,
        tool_calls: List[Any],
        current_step: int,
        on_tool_call: LifecycleHook = None
    ) -> List[tuple]:
        """
        异步并行执行工具（唯一异步执行入口，经 registry 熔断保护）

        策略：
        1. 内置工具（Thought/Finish）串行执行
        2. 用户工具并行执行（最多 max_concurrent_tools 个，走 registry.async_execute_tool）

        Args:
            tool_calls: 工具调用列表
            current_step: 当前步骤
            on_tool_call: 工具调用钩子

        Returns:
            [(tool_name, tool_call_id, result_dict), ...]
        """
        results = []

        # 分组：内置工具 vs 用户工具
        builtin_calls = [tc for tc in tool_calls if tc.function.name in self._builtin_tools]
        user_calls = [tc for tc in tool_calls if tc.function.name not in self._builtin_tools]

        # 1. 串行执行内置工具
        for tc in builtin_calls:
            tool_name = tc.function.name
            tool_call_id = tc.id

            try:
                arguments = parse_tool_arguments(tc)
            except json.JSONDecodeError as e:
                results.append((tool_name, tool_call_id, {"content": f"错误：参数格式不正确 - {str(e)}"}))
                continue

            # 触发工具调用事件
            await self._emit_event(
                EventType.TOOL_CALL,
                on_tool_call,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                args=arguments,
                step=current_step
            )

            result = self._handle_builtin_tool(tool_name, arguments)
            print(f"🔧 {tool_name}: {result['content']}")

            # 记录工具结果
            if self.trace_logger:
                self.trace_logger.log_event(
                    "tool_result",
                    {
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "status": "success",
                        "result": result['content']
                    },
                    step=current_step
                )

            results.append((tool_name, tool_call_id, result))

        # 2. 并行执行用户工具
        if user_calls:
            max_concurrent = getattr(self.config, 'max_concurrent_tools', 3)

            # 使用 Semaphore 限制并发数
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
                        step=current_step
                    )

                    print(f"🎬 调用工具: {tool_name}({arguments})")

                    # 统一经 registry 异步执行（含熔断检查/观测埋点/结果记录）
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

                    # 记录工具结果
                    if self.trace_logger:
                        self.trace_logger.log_event(
                            "tool_result",
                            {
                                "tool_name": tool_name,
                                "tool_call_id": tool_call_id,
                                "result": result_content
                            },
                            step=current_step
                        )

                    if result_content.startswith("❌"):
                        print(result_content)
                    else:
                        print(f"👀 观察: {result_content}")

                    return (tool_name, tool_call_id, {"content": result_content})

            # 并行执行
            user_results = await asyncio.gather(*[execute_one(tc) for tc in user_calls])
            results.extend(user_results)

        return results

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
        ReActAgent 真正的流式执行

        实时返回：
        - LLM 输出的每个文本块
        - 工具调用的开始和结束
        - 步骤的开始和结束

        Args:
            input_text: 用户问题
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
            messages = self._build_messages(input_text)
            tool_schemas = self._build_tool_schemas()

            current_step: int = 0
            final_answer = None

            print(f"\n🤖 {self.name} 开始处理问题: {input_text}")

            while current_step < self.max_steps:  # type: ignore[operator]
                current_step += 1
                self._current_step = current_step

                # 发送步骤开始事件
                yield StreamEvent.create(
                    StreamEventType.STEP_START,
                    self.name,
                    step=current_step,
                    max_steps=self.max_steps
                )

                await self._emit_event(EventType.STEP_START, on_step, step=current_step)

                print(f"\n--- 第 {current_step} 步 ---")

                # 单次 LLM 调用：异步 + 工具支持（避免重复调用）
                try:
                    response = await self.llm.ainvoke_with_tools(
                        messages=messages,
                        tools=tool_schemas,
                        tool_choice="auto",
                        **kwargs,
                    )
                    response_message = response.choices[0].message
                    full_response = response_message.content or ""

                    # 将内容作为单个流式块发送（避免双 LLM 调用）
                    if full_response:
                        yield StreamEvent.create(
                            StreamEventType.LLM_CHUNK,
                            self.name,
                            chunk=full_response,
                            step=current_step,
                        )

                except Exception as e:
                    error_msg = f"LLM 调用失败: {str(e)}"
                    self.logger.warning(error_msg)

                    yield StreamEvent.create(
                        StreamEventType.ERROR,
                        self.name,
                        error=error_msg,
                        step=current_step,
                    )

                    await self._emit_event(EventType.AGENT_ERROR, on_error, error=error_msg)
                    break

                # 解析工具调用
                try:
                    tool_calls = response_message.tool_calls or []

                    if not tool_calls:
                        # 没有工具调用，直接返回
                        final_answer = full_response or "抱歉，我无法回答这个问题。"

                        yield StreamEvent.create(
                            StreamEventType.AGENT_FINISH,
                            self.name,
                            result=final_answer,
                            total_steps=current_step,
                        )

                        await self._emit_event(EventType.AGENT_FINISH, on_finish, result=final_answer)

                        # 保存到历史
                        self.add_message(Message(input_text, "user"))
                        self.add_message(Message(final_answer, "assistant"))

                        return

                    # 添加助手消息到历史
                    messages.append({
                        "role": "assistant",
                        "content": response_message.content,
                        "tool_calls": serialize_tool_calls(tool_calls)
                    })

                    # 执行工具调用
                    tool_results = await self._execute_tools_async(
                        tool_calls,
                        current_step,
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
                            step=current_step
                        )

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result_dict["content"]
                        })

                        # 检查是否是 Finish 工具
                        if tool_name == "Finish":
                            try:
                                # 按 tool_call_id 匹配正确的调用（避免 tool_calls[0] 误取）
                                matched = next(
                                    (tc for tc in tool_calls if tc.id == tool_call_id),
                                    None)
                                if matched is not None:
                                    args = parse_tool_arguments(matched)
                                    final_answer = args.get("answer", result_dict["content"])
                                else:
                                    final_answer = result_dict["content"]
                            except Exception:
                                final_answer = result_dict["content"]

                            yield StreamEvent.create(
                                StreamEventType.AGENT_FINISH,
                                self.name,
                                result=final_answer,
                                total_steps=current_step
                            )

                            await self._emit_event(EventType.AGENT_FINISH, on_finish, result=final_answer)

                            # 保存到历史
                            self.add_message(Message(input_text, "user"))
                            self.add_message(Message(final_answer, "assistant"))

                            return

                    # 发送步骤完成事件
                    yield StreamEvent.create(
                        StreamEventType.STEP_FINISH,
                        self.name,
                        step=current_step
                    )

                except Exception as e:
                    error_msg = f"工具执行失败: {str(e)}"
                    print(f"❌ {error_msg}")

                    yield StreamEvent.create(
                        StreamEventType.ERROR,
                        self.name,
                        error=error_msg,
                        step=current_step
                    )

                    await self._emit_event(EventType.AGENT_ERROR, on_error, error=error_msg)
                    break

            # 达到最大步数
            if not final_answer:
                final_answer = "抱歉，已达到最大步数限制，无法完成任务。"

                yield StreamEvent.create(
                    StreamEventType.AGENT_FINISH,
                    self.name,
                    result=final_answer,
                    total_steps=current_step,
                    max_steps_reached=True
                )

                await self._emit_event(EventType.AGENT_FINISH, on_finish, result=final_answer)

                # 保存到历史
                self.add_message(Message(input_text, "user"))
                self.add_message(Message(final_answer, "assistant"))

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
