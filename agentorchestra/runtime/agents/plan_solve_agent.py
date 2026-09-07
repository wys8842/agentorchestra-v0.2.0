"""Plan and Solve Agent实现 - 分解规划与逐步执行的智能体

：模块级函数现委派到 Agent 基类的统一实现（_build_tool_schemas /
_map_parameter_type / _execute_tool_call），消除 ~150 行重复代码；保留模块级符号作为
向后兼容别名。
"""

import json
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional, cast

from agentorchestra.runtime.core.agent import Agent
from agentorchestra.runtime.core.agent.lifecycle import LifecycleHook
from agentorchestra.runtime.core.config import Config
from agentorchestra.runtime.core.llm import SymphonyLLM
from agentorchestra.runtime.core.llm.streaming import StreamEvent, StreamEventType
from agentorchestra.runtime.core.message import Message
from agentorchestra.runtime.core.utils import parse_tool_arguments, serialize_tool_calls

if TYPE_CHECKING:
    from agentorchestra.capability.tools.registry import ToolRegistry


def _build_tool_schemas_from_registry(tool_registry: 'ToolRegistry') -> List[Dict[str, Any]]:
    """从工具注册表构建工具 JSON Schema。

    保留模块级符号作为向后兼容别名；新代码请直接用 `Agent._build_tool_schemas()`。
    """
    if not tool_registry:
        return []
    return Agent._build_tool_schemas(tool_registry)


def _map_parameter_type(param_type: str) -> str:
    """映射参数类型到 JSON Schema 类型。

    注：基类 `_map_parameter_type` 是 `@staticmethod`，直接委派。
    """
    return Agent._map_parameter_type(param_type)


def _execute_tool_call_from_registry(
    registry: 'ToolRegistry',
    tool_name: str,
    arguments: Dict[str, Any]
) -> str:
    """执行工具调用并返回字符串结果。

    由于 Agent._execute_tool_call 是实例方法且依赖 self.tool_registry，
    这里通过临时 setattr 桥接（最轻量、不增加胶水类）。
    """
    if not registry:
        return "❌ 错误：未配置工具注册表"
    _stub = _NoOpAgent._minimal_instance()
    _stub.tool_registry = registry
    return _stub._execute_tool_call(tool_name, arguments)


class _NoOpAgent(Agent):
    """仅作为模块级函数到 Agent 实例方法的桥接器（不参与业务运行）。

    使用 _minimal_instance 工厂绕过 __init__（避免 name/llm 必传约束）。
    """

    @classmethod
    def _minimal_instance(cls) -> "_NoOpAgent":
        """创建无参实例（绕过 __init__）。"""
        instance = cls.__new__(cls)
        return instance

    def run(self, input_text: str, **kwargs) -> str:  # type: ignore[override]
        raise NotImplementedError("_NoOpAgent 仅用于桥接，不应调用 run()")


class Planner:
    """规划器 - 负责将复杂问题分解为简单步骤（使用 Function Calling）"""

    def __init__(self, llm_client: SymphonyLLM, system_prompt: Optional[str] = None):
        self.llm_client = llm_client
        self.system_prompt = system_prompt or """你是一个顶级的AI规划专家。你的任务是将用户提出的复杂问题分解成一个由多个简单步骤组成的行动计划。
            请确保计划中的每个步骤都是一个独立的、可执行的子任务，并且严格按照逻辑顺序排列。"""

    def plan(self, question: str, **kwargs) -> List[str]:
        """
        生成执行计划（使用 Function Calling）

        Args:
            question: 要解决的问题
            **kwargs: LLM调用参数

        Returns:
            步骤列表
        """
        print("--- 正在生成计划 ---")

        # 定义计划生成工具
        plan_tool = {
            "type": "function",
            "function": {
                "name": "generate_plan",
                "description": "生成解决问题的分步计划",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "按顺序排列的执行步骤列表"
                        }
                    },
                    "required": ["steps"]
                }
            }
        }

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"请为以下问题生成详细的执行计划：\n\n{question}"}
        ]

        try:
            response = self.llm_client.invoke_with_tools(
                messages=messages,
                tools=[plan_tool],
                tool_choice={"type": "function", "function": {"name": "generate_plan"}},
                **kwargs
            )

            # {
            # "choices": [
            #     {
            #     "message": {
            #         "content": null,
            #         "tool_calls": [                                    ← 工具调用列表
            #         {
            #             "id": "call_abc123",
            #             "type": "function",
            #             "function": {
            #             "name": "generate_plan",                     ← 调了哪个工具
            #             "arguments": "{\"steps\": [\"分析配置\", \"生成方案\"]}"  ← 参数(字符串!)
            #             }
            #         }
            #         ]
            #     }
            #     }
            # ]
            # }

            response_message = response.choices[0].message

            # 提取工具调用结果
            if response_message.tool_calls:
                tool_call = response_message.tool_calls[0]
                arguments = parse_tool_arguments(tool_call)
                plan = arguments.get("steps", [])

                print("✅ 计划已生成:")
                for i, step in enumerate(plan, 1):
                    print(f"  {i}. {step}")

                return plan
            else:
                print("❌ 模型未返回计划工具调用")
                return []

        except Exception as e:
            print(f"❌ 生成计划时发生错误: {e}")
            return []

class Executor:
    """执行器 - 负责按计划逐步执行（支持 Function Calling）"""

    def __init__(
        self,
        llm_client: SymphonyLLM,
        system_prompt: Optional[str] = None,
        tool_registry: Optional['ToolRegistry'] = None,
        enable_tool_calling: bool = True,
        max_tool_iterations: int = 3
    ):
        self.llm_client = llm_client
        self.system_prompt = system_prompt or """你是一位顶级的AI执行专家。你的任务是严格按照给定的计划，一步步地解决问题。
            请专注于解决当前步骤，并输出该步骤的最终答案。"""
        self.tool_registry = tool_registry
        self.enable_tool_calling = enable_tool_calling and tool_registry is not None
        self.max_tool_iterations = max_tool_iterations

    def execute(self, question: str, plan: List[str], **kwargs) -> str:
        """
        按计划执行任务（支持 Function Calling）

        Args:
            question: 原始问题
            plan: 执行计划
            **kwargs: LLM调用参数

        Returns:
            最终答案
        """
        history: List[Dict[str, Any]] = []
        final_answer = ""

        print("\n--- 正在执行计划 ---")
        for i, step in enumerate(plan, 1):
            print(f"\n-> 正在执行步骤 {i}/{len(plan)}: {step}")

            # 构建上下文消息
            context = f"""# 原始问题:
                {question}

                # 完整计划:
                {self._format_plan(plan)}

                # 历史步骤与结果:
                {self._format_history(history) if history else "无"}

                # 当前步骤:
                {step}

                请执行当前步骤并给出结果。"""

            # 执行单个步骤（支持工具调用）
            response_text = self._execute_step(context, **kwargs)

            history.append({"step": step, "result": response_text})
            final_answer = response_text
            print(f"✅ 步骤 {i} 已完成，结果: {final_answer}")

        return final_answer

    def _format_plan(self, plan: List[str]) -> str:
        """格式化计划列表"""
        return "\n".join([f"{i}. {step}" for i, step in enumerate(plan, 1)])

    def _format_history(self, history: List[Dict[str, str]]) -> str:
        """格式化历史记录"""
        return "\n\n".join([f"步骤 {i}: {h['step']}\n结果: {h['result']}"
                           for i, h in enumerate(history, 1)])

    def _execute_step(self, context: str, **kwargs) -> str:
        """
        执行单个步骤（支持 Function Calling）

        Args:
            context: 上下文信息
            **kwargs: 其他参数

        Returns:
            步骤执行结果
        """
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": context}
        ]

        # 如果没有启用工具调用，直接返回
        if not self.enable_tool_calling or not self.tool_registry:
            llm_response = self.llm_client.invoke(messages, **kwargs)
            return cast(str, llm_response.content) if hasattr(llm_response, 'content') else str(llm_response)

        # 启用工具调用模式 - 直接从注册表构建 schema，不创建临时 Agent
        tool_schemas = _build_tool_schemas_from_registry(self.tool_registry)

        current_iteration = 0

        while current_iteration < self.max_tool_iterations:
            current_iteration += 1

            try:
                response = self.llm_client.invoke_with_tools(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice="auto",
                    **kwargs
                )
            except Exception as e:
                print(f"❌ LLM 调用失败: {e}")
                break

            response_message = response.choices[0].message

            # 处理工具调用
            tool_calls = response_message.tool_calls
            if not tool_calls:
                # 没有工具调用，返回文本响应
                return response_message.content or ""

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

                # 执行工具（使用模块级函数，替代临时 Agent 模式）
                result = _execute_tool_call_from_registry(
                    self.tool_registry, tool_name, arguments)

                # 添加工具结果到消息
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result
                })

        # 如果超过最大迭代次数，获取最后一次回答
        if current_iteration >= self.max_tool_iterations:
            llm_response = self.llm_client.invoke(messages, **kwargs)
            return cast(str, llm_response.content) if hasattr(llm_response, 'content') else str(llm_response)

        return ""

class PlanSolveAgent(Agent):
    """
    Plan and Solve Agent - 分解规划与逐步执行的智能体

    这个Agent能够：
    1. 将复杂问题分解为简单步骤（使用 Function Calling）
    2. 按照计划逐步执行
    3. 维护执行历史和上下文
    4. 得出最终答案
    5. 支持工具调用（可选）

    特别适合多步骤推理、数学问题、复杂分析等任务。
    """

    def __init__(
        self,
        name: str,
        llm: SymphonyLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        planner_prompt: Optional[str] = None,
        executor_prompt: Optional[str] = None,
        tool_registry: Optional['ToolRegistry'] = None,
        enable_tool_calling: bool = True,
        max_tool_iterations: int = 3
    ):
        """
        初始化PlanSolveAgent

        Args:
            name: Agent名称
            llm: LLM实例
            system_prompt: 系统提示词（Agent级别）
            config: 配置对象
            planner_prompt: 规划器的系统提示词（可选）
            executor_prompt: 执行器的系统提示词（可选）
            tool_registry: 工具注册表（可选）
            enable_tool_calling: 是否启用工具调用
            max_tool_iterations: 最大工具调用迭代次数
        """
        # 传递 tool_registry 到基类
        super().__init__(
            name,
            llm,
            system_prompt,
            config,
            tool_registry=tool_registry
        )

        self.planner = Planner(self.llm, planner_prompt)
        self.executor = Executor(
            self.llm,
            executor_prompt,
            tool_registry=tool_registry,
            enable_tool_calling=enable_tool_calling,
            max_tool_iterations=max_tool_iterations
        )

    def run(self, input_text: str, **kwargs) -> str:
        """
        运行Plan and Solve Agent

        Args:
            input_text: 要解决的问题
            **kwargs: 其他参数

        Returns:
            最终答案
        """
        print(f"\n🤖 {self.name} 开始处理问题: {input_text}")

        # 记录会话开始
        if self.trace_logger:
            self.trace_logger.log_event(
                "session_start",
                {
                    "agent_name": self.name,
                    "agent_type": self.__class__.__name__,
                }
            )

        # 记录用户消息
        if self.trace_logger:
            self.trace_logger.log_event(
                "message_written",
                {"role": "user", "content": input_text}
            )

        # 1. 生成计划
        plan = self.planner.plan(input_text, **kwargs)
        if not plan:
            final_answer = "无法生成有效的行动计划，任务终止。"
            print(f"\n--- 任务终止 ---\n{final_answer}")

            # 保存到历史记录
            self.add_message(Message(input_text, "user"))
            self.add_message(Message(final_answer, "assistant"))

            if self.trace_logger:
                self.trace_logger.log_event(
                    "session_end",
                    {
                        "status": "failed",
                        "final_answer": final_answer,
                    }
                )
                self.trace_logger.finalize()

            return final_answer

        # 2. 执行计划
        final_answer = self.executor.execute(input_text, plan, **kwargs)
        print(f"\n--- 任务完成 ---\n最终答案: {final_answer}")

        # 保存到历史记录
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_answer, "assistant"))

        if self.trace_logger:
            self.trace_logger.log_event(
                "session_end",
                {
                    "status": "success",
                    "final_answer": final_answer,
                }
            )
            self.trace_logger.finalize()

        return final_answer

    async def arun_stream(  # type: ignore[override]
        self,
        input_text: str,
        on_start: LifecycleHook = None,
        on_finish: LifecycleHook = None,
        on_error: LifecycleHook = None,
        **kwargs
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        PlanAgent 真正的流式执行

        实时返回：
        - 规划阶段的计划生成
        - 执行阶段的每个步骤输出

        Args:
            input_text: 用户输入
            on_start: 开始钩子
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

        try:
            # 阶段 1：规划
            yield StreamEvent.create(
                StreamEventType.STEP_START,
                self.name,
                phase="planning",
                description="生成执行计划"
            )

            print(f"\n🤖 {self.name} 开始处理问题: {input_text}")

            # 生成计划（同步方法，暂时保持）
            plan = self.planner.plan(input_text, **kwargs)

            if not plan:
                error_msg = "无法生成有效的行动计划，任务终止。"

                yield StreamEvent.create(
                    StreamEventType.ERROR,
                    self.name,
                    error=error_msg,
                    phase="planning"
                )

                yield StreamEvent.create(
                    StreamEventType.AGENT_FINISH,
                    self.name,
                    result=error_msg
                )

                self.add_message(Message(input_text, "user"))
                self.add_message(Message(error_msg, "assistant"))
                return

            yield StreamEvent.create(
                StreamEventType.STEP_FINISH,
                self.name,
                phase="planning",
                plan=plan,
                total_steps=len(plan)
            )

            # 阶段 2：执行计划
            step_results: List[str] = []

            for i, step_description in enumerate(plan):
                step_num = i + 1

                # 步骤开始
                yield StreamEvent.create(
                    StreamEventType.STEP_START,
                    self.name,
                    phase="execution",
                    step=step_num,
                    total_steps=len(plan),
                    description=step_description
                )

                print(f"\n--- 步骤 {step_num}/{len(plan)} ---")
                print(f"📋 {step_description}")

                # 构建执行提示
                context = "\n".join([
                    f"步骤 {j+1}: {plan[j]} -> {step_results[j]}"
                    for j in range(len(step_results))
                ])

                prompt = f"""原始问题: {input_text}

                完整计划:
                {chr(10).join([f"{j+1}. {s}" for j, s in enumerate(plan)])}

                已完成的步骤:
                {context if context else "无"}

                当前步骤: {step_description}

                请执行当前步骤并给出结果。"""

                messages = [{"role": "user", "content": prompt}]

                # 流式执行步骤
                step_result = ""
                async for chunk in self.llm.astream_invoke(messages, **kwargs):
                    step_result += chunk

                    yield StreamEvent.create(
                        StreamEventType.LLM_CHUNK,
                        self.name,
                        chunk=chunk,
                        phase="execution",
                        step=step_num
                    )

                    print(chunk, end="", flush=True)

                print()  # 换行

                step_results.append(step_result)

                # 步骤完成
                yield StreamEvent.create(
                    StreamEventType.STEP_FINISH,
                    self.name,
                    phase="execution",
                    step=step_num,
                    result=step_result
                )

            # 生成最终答案
            yield StreamEvent.create(
                StreamEventType.STEP_START,
                self.name,
                phase="final_answer",
                description="生成最终答案"
            )

            final_prompt = f"""原始问题: {input_text}

                执行计划和结果:
                {chr(10).join([f"{i+1}. {plan[i]} -> {step_results[i]}" for i in range(len(plan))])}

                请基于以上步骤的执行结果，给出原始问题的最终答案。"""

            final_messages = [{"role": "user", "content": final_prompt}]

            final_answer = ""
            async for chunk in self.llm.astream_invoke(final_messages, **kwargs):
                final_answer += chunk

                yield StreamEvent.create(
                    StreamEventType.LLM_CHUNK,
                    self.name,
                    chunk=chunk,
                    phase="final_answer"
                )

            # 发送完成事件
            yield StreamEvent.create(
                StreamEventType.AGENT_FINISH,
                self.name,
                result=final_answer,
                total_steps=len(plan)
            )

            print(f"\n--- 任务完成 ---\n最终答案: {final_answer}")

            # 保存到历史
            self.add_message(Message(input_text, "user"))
            self.add_message(Message(final_answer, "assistant"))

        except Exception as e:
            # 发送错误事件
            yield StreamEvent.create(
                StreamEventType.ERROR,
                self.name,
                error=str(e),
                error_type=type(e).__name__
            )
            raise
