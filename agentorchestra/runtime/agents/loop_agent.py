"""Loop Agent - 闭环认知循环（Plan → Act → Observe → Reflect → Check → Replan）

基于技术路线文档实现显式认知闭环：
- Plan：结构化计划
- Act：工具执行
- Observe：证据沉淀
- Reflect：反思与状态管理
- Check：多信号终止判定
- Replan：再规划

默认关闭所有新特性，向后兼容现有行为。
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

from ..core.agent import Agent
from ..core.config import Config
from ..core.lifecycle import EventType, LifecycleHook
from ..core.llm import SymphonyLLM
from ..core.message import Message
from ..core.streaming import StreamEvent, StreamEventType
from ..core.utils import duration_seconds, parse_tool_arguments, serialize_tool_calls

if TYPE_CHECKING:
    from agentorchestra.capability.tools.registry import ToolRegistry


# ==================== 核心数据类 ====================


class LoopStatus(Enum):
    """循环状态"""
    RUNNING = "running"
    STOPPED = "stopped"
    REPLANNING = "replanning"
    ERROR = "error"


@dataclass
class Plan:
    """结构化计划"""
    steps: List[str] = field(default_factory=list)
    current_step: int = 0
    open_questions: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": self.steps,
            "current_step": self.current_step,
            "open_questions": self.open_questions,
            "success_criteria": self.success_criteria,
        }


@dataclass
class Evidence:
    """工具执行证据"""
    tool_name: str
    tool_call_id: str
    status: str  # success / error / partial
    truncated: bool = False
    summary: str = ""
    supports_goal: bool = True
    contradictions: List[str] = field(default_factory=list)
    next_info_gap: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "truncated": self.truncated,
            "summary": self.summary,
            "supports_goal": self.supports_goal,
            "contradictions": self.contradictions,
            "next_info_gap": self.next_info_gap,
        }


@dataclass
class Reflection:
    """反思结果"""
    progress: float = 0.0  # 0~1
    issues: List[str] = field(default_factory=list)
    next_strategy: Optional[str] = None
    should_replan: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "progress": self.progress,
            "issues": self.issues,
            "next_strategy": self.next_strategy,
            "should_replan": self.should_replan,
        }


@dataclass
class Budget:
    """预算控制"""
    max_steps: int = 5
    max_replans: int = 2
    current_steps: int = 0
    current_replans: int = 0


@dataclass
class TerminationDecision:
    """终止决策"""
    signal: str  # completed / stuck / errors / budget / no_progress / terminate_tool / running
    action: str  # stop / replan / continue
    reason: str = ""


@dataclass
class LoopState:
    """循环状态"""
    goal: str
    plan: Plan = field(default_factory=Plan)
    evidence: List[Evidence] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    status: LoopStatus = LoopStatus.RUNNING
    reflection_history: List[Reflection] = field(default_factory=list)
    last_decision: Optional[TerminationDecision] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "plan": self.plan.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
            "budget": {
                "max_steps": self.budget.max_steps,
                "max_replans": self.budget.max_replans,
                "current_steps": self.budget.current_steps,
                "current_replans": self.budget.current_replans,
            },
            "status": self.status.value,
            "reflection_history": [r.to_dict() for r in self.reflection_history],
            "last_decision": self.last_decision.to_dict() if self.last_decision else None,
        }


# ==================== LoopAgent 主类 ====================


class LoopAgent(Agent):
    """
    Loop Agent - 闭环认知循环执行智能体

    支持两种模式：
    - 简单模式（默认）：无工具调用即停 + max_steps 上限
    - 完整模式（enable_reflection=True）：Plan → Act → Observe → Reflect → Check → Replan

    适用场景：
    - 需要多轮工具交互的任务
    - 逻辑不确定、需要多次查询的问题
    - 需要显式计划和反思的复杂任务
    """

    def __init__(
        self,
        name: str,
        llm: SymphonyLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        tool_registry: Optional['ToolRegistry'] = None,
        enable_tool_calling: bool = True,
        max_steps: int = 5,
        # 新特性开关（默认关闭）
        enable_reflection: bool = False,
        enable_replan: bool = False,
        max_replans: int = 2,
        max_consecutive_errors: int = 3,
        stuck_threshold: int = 2,
        reflection_interval: int = 1,
    ):
        """
        初始化 LoopAgent

        Args:
            name: Agent名称
            llm: LLM实例
            system_prompt: 系统提示词
            config: 配置对象
            tool_registry: 工具注册表
            enable_tool_calling: 是否启用工具调用
            max_steps: 最大迭代步数
            enable_reflection: 是否启用反思（默认关闭）
            enable_replan: 是否启用再规划（默认关闭）
            max_replans: 最大再规划次数
            max_consecutive_errors: 连续错误上限
            stuck_threshold: 卡死检测阈值
            reflection_interval: 反思频率（每N步）
        """
        super().__init__(
            name,
            llm,
            system_prompt,
            config,
            tool_registry=tool_registry
        )
        self.enable_tool_calling = enable_tool_calling and tool_registry is not None
        self.max_steps = max_steps

        # 新特性开关（默认关闭，向后兼容）
        self.enable_reflection = enable_reflection
        self.enable_replan = enable_replan
        self.max_replans = max_replans
        self.max_consecutive_errors = max_consecutive_errors
        self.stuck_threshold = stuck_threshold
        self.reflection_interval = reflection_interval

    # ==================== 入口方法 ====================

    def run(self, input_text: str, **kwargs) -> str:
        """运行 LoopAgent（同步入口）"""
        from datetime import datetime
        session_start_time = datetime.now()

        messages = self._build_messages(input_text)

        if self.trace_logger:
            self.trace_logger.log_event("session_start", {
                "agent_name": self.name,
                "agent_type": self.__class__.__name__,
            })
            self.trace_logger.log_event("message_written", {
                "role": "user",
                "content": input_text
            })

        # 简单模式：直接循环
        if not self.enable_reflection and not self.enable_replan:
            return self._run_simple(input_text, messages, session_start_time, **kwargs)

        # 完整模式：闭环认知
        return self._run_loop(input_text, messages, session_start_time, **kwargs)

    def _run_simple(
        self,
        input_text: str,
        messages: List[Dict[str, Any]],
        session_start_time: datetime,
        **kwargs
    ) -> str:
        """简单模式：无反思/再规划"""
        if not self.enable_tool_calling or not self.tool_registry:
            return self._run_no_tools(messages, session_start_time, **kwargs)

        tool_schemas = self._build_tool_schemas()
        current_iteration = 0
        final_response = ""

        while current_iteration < self.max_steps:
            current_iteration += 1

            try:
                response = self.llm.invoke_with_tools(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice="auto",
                    **kwargs
                )
            except Exception as e:
                if self.trace_logger:
                    self.trace_logger.log_event("error", {
                        "error_type": "LLM_ERROR",
                        "message": str(e)
                    }, step=current_iteration)
                break

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            if not tool_calls:
                final_response = response_message.content or "抱歉，我无法回答这个问题。"
                break

            messages.append({
                "role": "assistant",
                "content": response_message.content,
                "tool_calls": serialize_tool_calls(tool_calls)
            })

            # 同步顺序执行
            for tc in tool_calls:
                result = self._run_tool_sync(tc, current_iteration)
                messages.append(result)

        if current_iteration >= self.max_steps and not final_response:
            response = self.llm.invoke(messages, **kwargs)
            final_response = response.content if hasattr(response, 'content') else str(response)

        return self._finalize(input_text, final_response, session_start_time, current_iteration)

    def _run_loop(
        self,
        input_text: str,
        messages: List[Dict[str, Any]],
        session_start_time: datetime,
        **kwargs
    ) -> str:
        """完整模式：闭环认知"""
        tool_schemas = self._build_tool_schemas()
        state = LoopState(
            goal=input_text,
            plan=Plan(),
            budget=Budget(max_steps=self.max_steps, max_replans=self.max_replans),
            messages=messages
        )

        current_iteration = 0
        final_response = ""

        while state.budget.current_steps < state.budget.max_steps:
            state.budget.current_steps += 1
            current_iteration += 1

            # PLAN
            if self.trace_logger:
                self.trace_logger.log_event("PLAN_START", {
                    "iteration": current_iteration
                }, step=current_iteration)

            response = self._plan(state, tool_schemas, **kwargs)

            # CHECK (before act)
            decision = self._check_done(state, bool(response.choices[0].message.tool_calls))
            if decision.action == "stop":
                final_response = response.choices[0].message.content or ""
                break

            # ACT
            if self.trace_logger:
                self.trace_logger.log_event("ACT_START", {
                    "iteration": current_iteration
                }, step=current_iteration)

            tool_results = self._run_tools_sync(
                response.choices[0].message.tool_calls or [],
                current_iteration
            )

            # OBSERVE
            self._observe(tool_results, state)

            # REFLECT
            if self.enable_reflection and current_iteration % self.reflection_interval == 0:
                reflection = self._reflect(state)
                state.reflection_history.append(reflection)

                if self.trace_logger:
                    self.trace_logger.log_event("REFLECT_FINISH", {
                        "progress": reflection.progress,
                        "should_replan": reflection.should_replan,
                        "issues": reflection.issues
                    }, step=current_iteration)

            # CHECK (after observe)
            decision = self._check_done(state, bool(tool_results))
            state.last_decision = decision

            if decision.action == "stop":
                final_response = response.choices[0].message.content or ""
                break

            if decision.action == "replan" and self.enable_replan:
                if state.budget.current_replans < state.budget.max_replans:
                    state.plan = self._replan(state)
                    state.budget.current_replans += 1
                    if self.trace_logger:
                        self.trace_logger.log_event("REPLAN_FINISH", {
                            "new_plan": state.plan.to_dict()
                        }, step=current_iteration)

        return self._finalize(input_text, final_response, session_start_time, current_iteration)

    # ==================== 阶段方法 ====================

    def _plan(
        self,
        state: LoopState,
        tool_schemas: List[Dict[str, Any]],
        **kwargs
    ):
        """Plan 阶段：生成或更新计划"""
        response = self.llm.invoke_with_tools(
            messages=state.messages,
            tools=tool_schemas,
            tool_choice="auto",
            **kwargs
        )
        return response

    def _observe(self, tool_results: List[Tuple], state: LoopState) -> None:
        """Observe 阶段：工具结果沉淀为 Evidence"""
        for name, call_id, result_dict in tool_results:
            evidence = Evidence(
                tool_name=name,
                tool_call_id=call_id,
                status="error" if result_dict.get("error") else "success",
                truncated=result_dict.get("truncated", False),
                summary=self._summarize_result(result_dict.get("content", "")),
            )
            state.evidence.append(evidence)

            state.messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result_dict.get("content", "")
            })

    def _reflect(self, state: LoopState) -> Reflection:
        """Reflect 阶段：反思当前进度"""
        if not state.evidence:
            return Reflection(progress=0.0, issues=[], should_replan=False)

        # 简单规则：基于证据数量估算进度
        progress = min(len(state.evidence) / state.budget.max_steps, 1.0)
        issues = []
        should_replan = False

        # 检测连续错误
        recent_errors = sum(
            1 for e in state.evidence[-self.stuck_threshold:]
            if e.status == "error"
        )
        if recent_errors >= self.stuck_threshold:
            issues.append(f"连续{recent_errors}个错误")
            should_replan = True

        return Reflection(
            progress=progress,
            issues=issues,
            should_replan=should_replan
        )

    def _check_done(self, state: LoopState, has_tool_calls: bool) -> TerminationDecision:
        """Check 阶段：多信号终止判定"""
        # 1) 预算耗尽
        if state.budget.current_steps >= state.budget.max_steps:
            return TerminationDecision("budget", "stop", "max_steps reached")

        # 2) 无工具调用且有证据
        if not has_tool_calls and state.evidence:
            return TerminationDecision("no_progress", "stop", "no further action")

        # 3) 连续错误
        recent_errors = sum(
            1 for e in state.evidence[-self.max_consecutive_errors:]
            if e.status == "error"
        )
        if recent_errors >= self.max_consecutive_errors:
            return TerminationDecision("errors", "stop", "too many tool errors")

        return TerminationDecision("running", "continue", "")

    def _replan(self, state: LoopState) -> Plan:
        """Replan 阶段：更新计划"""
        # 简单实现：清空当前步骤，让模型重新规划
        return Plan()

    def _summarize_result(self, content: str) -> str:
        """总结工具结果"""
        if not content:
            return ""
        # 简单截断
        return content[:200] + "..." if len(content) > 200 else content

    # ==================== 工具执行 ====================

    def _run_tool_sync(self, tool_call, iteration: int) -> Dict[str, Any]:
        """同步执行单个工具"""
        tool_name = tool_call.function.name
        tool_call_id = tool_call.id

        try:
            arguments = parse_tool_arguments(tool_call)
        except json.JSONDecodeError as e:
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": f"错误：参数格式不正确 - {str(e)}",
                "error": True
            }

        if self.tool_registry:
            response = self.tool_registry.execute_tool(tool_name, json.dumps(arguments))
            content = response.text
        else:
            content = f"工具 {tool_name} 未注册"

        if self.trace_logger:
            self.trace_logger.log_event("tool_result", {
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "result": content
            }, step=iteration)

        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content
        }

    def _run_tools_sync(self, tool_calls, iteration: int) -> List[Tuple[str, str, Dict[str, Any]]]:
        """同步执行多个工具（顺序）"""
        results = []
        for tc in tool_calls:
            result = self._run_tool_sync(tc, iteration)
            results.append((tc.function.name, tc.id, result))
        return results

    # ==================== 辅助方法 ====================

    def _run_no_tools(self, messages, session_start_time, **kwargs):
        """无工具模式"""
        response = self.llm.invoke(messages, **kwargs)
        final_response = response.content if hasattr(response, 'content') else str(response)
        return self._finalize("", final_response, session_start_time, 0)

    def _finalize(
        self,
        input_text: str,
        final_response: str,
        session_start_time: datetime,
        total_steps: int
    ) -> str:
        """收尾：保存历史 + 记录 trace"""
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_response, "assistant"))

        if self.trace_logger:
            duration = duration_seconds(session_start_time)
            self.trace_logger.log_event("session_end", {
                "duration": duration,
                "total_steps": total_steps,
                "final_answer": final_response,
                "status": "success"
            })
            self.trace_logger.finalize()

        return final_response

    def _build_messages(self, input_text: str) -> List[Dict[str, Any]]:
        """构建消息列表"""
        messages = []

        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        for msg in self._history:
            if msg.role in ("user", "assistant", "system", "tool"):
                messages.append({"role": msg.role, "content": msg.content})

        gssc_context = self._build_context(input_text, history=self._history)
        user_content = gssc_context if gssc_context is not None else input_text
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_tool_schemas(self) -> List[Dict[str, Any]]:
        """构建工具 JSON Schema"""
        schemas = []

        # 内置 terminate 工具
        schemas.append({
            "type": "function",
            "function": {
                "name": "terminate",
                "description": "任务已完成，调用此工具终止循环。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "终止原因"
                        }
                    }
                }
            }
        })

        if self.tool_registry:
            user_schemas = super()._build_tool_schemas()
            schemas.extend(user_schemas)

        return schemas

    # ==================== 流式执行 ====================

    async def arun_stream(
        self,
        input_text: str,
        on_start: LifecycleHook = None,
        on_step: LifecycleHook = None,
        on_tool_call: LifecycleHook = None,
        on_finish: LifecycleHook = None,
        on_error: LifecycleHook = None,
        **kwargs
    ) -> AsyncGenerator[StreamEvent, None]:
        """流式执行（异步）"""
        yield StreamEvent.create(StreamEventType.AGENT_START, self.name, input_text=input_text)
        await self._emit_event(EventType.AGENT_START, on_start, input_text=input_text)

        messages = self._build_messages(input_text)
        tool_schemas = self._build_tool_schemas()

        state = LoopState(
            goal=input_text,
            plan=Plan(),
            budget=Budget(max_steps=self.max_steps, max_replans=self.max_replans),
            messages=messages
        )

        while state.budget.current_steps < state.budget.max_steps:
            state.budget.current_steps += 1
            iteration = state.budget.current_steps

            yield StreamEvent.create(StreamEventType.STEP_START, self.name, step=iteration)

            # PLAN
            response = await self._plan_async(state, tool_schemas, **kwargs)
            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls or []

            # CHECK
            decision = self._check_done(state, bool(tool_calls))
            if decision.action == "stop":
                yield StreamEvent.create(
                    StreamEventType.AGENT_FINISH,
                    self.name,
                    result=response_message.content or "",
                    total_steps=iteration
                )
                break

            # ACT
            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": response_message.content,
                    "tool_calls": serialize_tool_calls(tool_calls)
                })

                tool_results = await self._execute_tools_async(tool_calls, iteration, on_tool_call)

                for name, call_id, result_dict in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result_dict.get("content", "")
                    })
                    yield StreamEvent.create(
                        StreamEventType.TOOL_CALL_FINISH,
                        self.name,
                        tool_name=name,
                        tool_call_id=call_id,
                        result=result_dict.get("content", ""),
                        step=iteration
                    )

                self._observe(tool_results, state)

            yield StreamEvent.create(StreamEventType.STEP_FINISH, self.name, step=iteration)

        await self._emit_event(EventType.AGENT_FINISH, on_finish, result="")

    async def _plan_async(self, state: LoopState, tool_schemas, **kwargs):
        """异步 Plan"""
        return await self.llm.ainvoke_with_tools(
            messages=state.messages,
            tools=tool_schemas,
            tool_choice="auto",
            **kwargs
        )

    async def _execute_tools_async(
        self,
        tool_calls: List[Any],
        iteration: int,
        on_tool_call: LifecycleHook = None
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """异步并行执行工具"""
        results = []
        max_concurrent = getattr(self.config, 'max_concurrent_tools', 3)
        semaphore = asyncio.Semaphore(max_concurrent)

        async def execute_one(tc):
            async with semaphore:
                tool_name = tc.function.name
                tool_call_id = tc.id

                try:
                    arguments = parse_tool_arguments(tc)
                except json.JSONDecodeError as e:
                    return (tool_name, tool_call_id, {"content": f"错误：{str(e)}"})

                await self._emit_event(EventType.TOOL_CALL, on_tool_call,
                                      tool_name=tool_name, tool_call_id=tool_call_id,
                                      args=arguments, step=iteration)

                if self.tool_registry:
                    response = await self.tool_registry.async_execute_tool(
                        tool_name, json.dumps(arguments)
                    )
                    content = response.text
                else:
                    content = f"工具 {tool_name} 未注册"

                return (tool_name, tool_call_id, {"content": content})

        gathered = await asyncio.gather(*[execute_one(tc) for tc in tool_calls])
        return list(gathered)

    # ==================== 工具管理 ====================

    def register_tool(self, tool, auto_expand: bool = True) -> None:
        """注册工具"""
        if not self.tool_registry:
            from agentorchestra.capability.tools.registry import ToolRegistry
            self.tool_registry = ToolRegistry()
            self.enable_tool_calling = True
        self.tool_registry.register_tool(tool, auto_expand=auto_expand)

    def unregister_tool(self, tool_name: str) -> bool:
        """取消注册工具"""
        if self.tool_registry:
            return self.tool_registry.unregister(tool_name)
        return False

    def list_tools(self) -> list:
        """列出工具"""
        if self.tool_registry:
            return self.tool_registry.list_tools()
        return []

    def has_tools(self) -> bool:
        """检查是否有工具"""
        return self.enable_tool_calling and self.tool_registry is not None
