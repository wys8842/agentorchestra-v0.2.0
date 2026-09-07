"""ReAct 执行器 - 抽象 ReAct 循环核心逻辑。

抽取 run / arun / arun_stream 三套循环的公共逻辑：
- 消息构建
- LLM 调用
- 工具执行
- 结果处理
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC
from typing import Any, Dict, List, Optional

from agentorchestra.capability.tools.registry import ToolRegistry
from agentorchestra.runtime.core.llm import SymphonyLLM
from agentorchestra.runtime.core.utils import parse_tool_arguments

from .builtin_tools import BuiltinTools


class ReActExecutor(ABC):
    """ReAct 执行器抽象 - 抽取三套循环的公共逻辑

    子类实现具体的执行方式：
    - 同步执行 (run)
    - 异步执行 (arun)
    - 流式执行 (arun_stream)
    """

    def __init__(
        self,
        name: str,
        llm: SymphonyLLM,
        tool_registry: ToolRegistry,
        system_prompt: str,
        max_steps: int,
    ):
        self.name = name
        self.llm = llm
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self._builtin_tools = BuiltinTools()

    # ==================== 公共方法（供子类调用） ====================

    def build_messages(self, input_text: str, gssc_context: Optional[str] = None) -> List[Dict[str, Any]]:
        """构建消息列表"""
        messages = []

        if self.system_prompt:
            messages.append({
                "role": "system",
                "content": self.system_prompt
            })

        user_content = gssc_context if gssc_context is not None else input_text
        messages.append({
            "role": "user",
            "content": user_content
        })

        return messages

    def build_tool_schemas(self) -> List[Dict[str, Any]]:
        """构建工具 JSON Schema（包含内置工具和用户工具）"""
        schemas = []

        # 内置工具：Thought
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

        # 内置工具：Finish
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

        # 用户工具
        if self.tool_registry:
            schemas.extend(self.tool_registry.list_tools())

        return schemas

    def handle_builtin_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """处理内置工具调用"""
        if tool_name == "Thought":
            return {
                "content": f"推理: {arguments.get('reasoning', '')}",
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

    async def execute_tools_async(
        self,
        tool_calls: List[Any],
        current_step: int,
    ) -> List[tuple]:
        """异步并行执行工具

        策略：
        1. 内置工具（Thought/Finish）串行执行
        2. 用户工具并行执行
        """
        results = []

        builtin_calls = [tc for tc in tool_calls if tc.function.name in {"Thought", "Finish"}]
        user_calls = [tc for tc in tool_calls if tc.function.name not in {"Thought", "Finish"}]

        # 1. 串行执行内置工具
        for tc in builtin_calls:
            tool_name = tc.function.name
            tool_call_id = tc.id

            try:
                arguments = parse_tool_arguments(tc)
            except json.JSONDecodeError as e:
                results.append((tool_name, tool_call_id, {"content": f"错误：参数格式不正确 - {str(e)}"}))
                continue

            result = self.handle_builtin_tool(tool_name, arguments)
            results.append((tool_name, tool_call_id, result))

        # 2. 并行执行用户工具
        if user_calls:
            max_concurrent = 3

            async def execute_one(tc):
                tool_name = tc.function.name
                tool_call_id = tc.id

                try:
                    arguments = parse_tool_arguments(tc)
                except json.JSONDecodeError as e:
                    return (tool_name, tool_call_id, {"content": f"错误：参数格式不正确 - {str(e)}"})

                tool_response = await self.tool_registry.async_execute_tool(tool_name, arguments)
                return (tool_name, tool_call_id, {"content": tool_response.text})

            semaphore = asyncio.Semaphore(max_concurrent)

            async def execute_with_semaphore(tc):
                async with semaphore:
                    return await execute_one(tc)

            user_results = await asyncio.gather(*[execute_with_semaphore(tc) for tc in user_calls])
            results.extend(user_results)

        return results

    def check_finish(self, tool_results: List[tuple]) -> Optional[Dict[str, Any]]:
        """检查是否有 Finish 工具调用"""
        for tool_name, tool_call_id, result in tool_results:
            if tool_name == "Finish" and result.get("finished"):
                return {
                    "final_answer": result.get("final_answer"),
                    "tool_call_id": tool_call_id
                }
        return None

    def add_messages(self, messages: List[Dict], role: str, content: str, **kwargs):
        """添加工具消息到历史"""
        msg = {"role": role, "content": content}
        if kwargs:
            msg.update(kwargs)
        messages.append(msg)


# ==================== 内置工具类 ====================

class BuiltinTools:
    """ReAct 内置工具集合"""

    @staticmethod
    def thought(reasoning: str) -> str:
        return f"推理: {reasoning}"

    @staticmethod
    def finish(answer: str) -> Dict[str, Any]:
        return {
            "content": f"最终答案: {answer}",
            "finished": True,
            "final_answer": answer
        }
