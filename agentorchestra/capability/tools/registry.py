"""工具注册表 - Symphony原生工具系统"""

import asyncio
import contextlib
import contextvars
import json
import logging
import time
from typing import Any, Callable, Dict, Iterator, Optional

from agentorchestra.runtime.core.utils import measure_elapsed_ms

from .base import Tool
from .circuit_breaker import CircuitBreaker
from .errors import ToolErrorCode
from .response import ToolResponse

logger = logging.getLogger("agentorchestra.tools.registry")


# ---------------- 子代理安全：contextvars RAII ----------------

# 每个 asyncio 任务独立持有 disabled_tools 集合
_disabled_tools_var: contextvars.ContextVar[set[str]] = contextvars.ContextVar(
    "_disabled_tools", default=set()
)
_disabled_functions_var: contextvars.ContextVar[set[str]] = contextvars.ContextVar(
    "_disabled_functions", default=set()
)


@contextlib.contextmanager
def temporary_tool_filter(disabled_tool_names: set[str]) -> Iterator[None]:
    """在 with 块内临时禁用指定工具；块退出自动恢复（finally 强保证）。


    contextvars + RAII 强保证 finally 块执行。

    用法：
        with temporary_tool_filter({"dangerous_tool", "write_file"}):
            result = sub_agent.run(task)
        # 块退出后工具自动恢复
    """
    prev_tools = _disabled_tools_var.get()
    prev_funcs = _disabled_functions_var.get()
    tok_tools = _disabled_tools_var.set(
        (prev_tools or set()) | set(disabled_tool_names)
    )
    tok_funcs = _disabled_functions_var.set(
        (prev_funcs or set()) | set(disabled_tool_names)
    )
    try:
        yield
    finally:
        _disabled_tools_var.reset(tok_tools)
        _disabled_functions_var.reset(tok_funcs)


def _is_error_response(response: ToolResponse) -> bool:
    """判断响应是否为错误状态"""
    return response.status is not None and getattr(response.status, "value", "") == "error"


def _wrap_function_response(result: Any, elapsed_ms: float, tool_name: str, input_text: str) -> ToolResponse:
    """将函数执行结果包装为 ToolResponse（用于函数工具路径的 timing 封装复用）"""
    return ToolResponse.success(
        text=str(result),
        data={"output": result},
        stats={"time_ms": elapsed_ms},
        context={"tool_name": tool_name, "input": input_text}
    )


class ToolRegistry:
    """
    Symphony工具注册表

    提供工具的注册、管理和执行功能。
    支持两种工具注册方式：
    1. Tool对象注册（推荐）
    2. 函数直接注册（简便）
    """

    def __init__(self, circuit_breaker: Optional[CircuitBreaker] = None):
        self._tools: dict[str, Tool] = {}
        self._functions: dict[str, dict[str, Any]] = {}

        # 文件元数据缓存（用于乐观锁机制）
        self.read_metadata_cache: Dict[str, Dict[str, Any]] = {}

        # 熔断器（默认启用）
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

    def register_tool(self, tool: Tool, auto_expand: bool = True):
        """
        注册Tool对象

        Args:
            tool: Tool实例
            auto_expand: 是否自动展开可展开的工具（默认True）
        """
        # 检查工具是否可展开
        if auto_expand and hasattr(tool, 'expandable') and tool.expandable:
            expanded_tools = tool.get_expanded_tools()
            if expanded_tools:
                # 注册所有展开的子工具
                for sub_tool in expanded_tools:
                    if sub_tool.name in self._tools:
                        logger.warning("工具 %s 已存在，将被覆盖", sub_tool.name)
                    self._tools[sub_tool.name] = sub_tool
                logger.debug("工具 %s 已展开为 %d 个独立工具", tool.name, len(expanded_tools))
                return

        # 普通工具或不展开的工具
        if tool.name in self._tools:
            logger.warning("工具 %s 已存在，将被覆盖", tool.name)

        self._tools[tool.name] = tool
        logger.debug("工具 %s 已注册", tool.name)

    def register_function(
        self,
        func: Callable,
        name: Optional[str] = None,
        description: Optional[str] = None
    ):
        """
        直接注册函数作为工具（简便方式）

        支持两种调用方式：
        1. 传统方式：register_function(name, description, func)
        2. 新方式：register_function(func, name=None, description=None)
           - 自动从函数名和 docstring 提取信息

        Args:
            func: 工具函数
            name: 工具名称（可选，默认使用函数名）
            description: 工具描述（可选，默认使用函数 docstring）

        使用示例:
            >>> def my_tool(input: str) -> str:
            ...     '''这是我的工具'''
            ...     return f"处理: {input}"
            >>> registry.register_function(my_tool)
            >>> # 或者指定名称和描述
            >>> registry.register_function(my_tool, name="custom_name", description="自定义描述")
        """
        # 兼容旧的调用方式：register_function(name, description, func)
        if isinstance(func, str) and callable(description):
            # 旧方式：第一个参数是 name，第二个是 description，第三个是 func
            name, description, func = func, name, description

        # 自动提取名称
        if name is None:
            name = func.__name__

        # 自动提取描述
        if description is None:
            import inspect
            doc = inspect.getdoc(func)
            if doc:
                # 提取第一行作为描述
                description = doc.split('\n')[0].strip()
            else:
                description = f"执行 {name}"

        if name in self._functions:
            logger.warning("函数工具 %s 已存在，将被覆盖", name)

        self._functions[name] = {
            "description": description,
            "func": func
        }
        logger.debug("函数工具 %s 已注册", name)

    def unregister(self, name: str):
        """注销工具"""
        if name in self._tools:
            del self._tools[name]
            logger.info("工具 %s 已注销", name)
        elif name in self._functions:
            del self._functions[name]
            logger.info("工具 %s 已注销", name)
        else:
            logger.warning("工具 %s 不存在", name)

    def get_tool(self, name: str) -> Optional[Tool]:
        """获取Tool对象"""
        return self._tools.get(name)

    def get_function(self, name: str) -> Optional[Callable]:
        """获取工具函数"""
        func_info = self._functions.get(name)
        return func_info["func"] if func_info else None

    @staticmethod
    def _parse_input(input_text: Any) -> Dict[str, Any]:
        """解析工具输入参数为参数字典

        支持：
        - 已解析的字典
        - JSON 字符串（要求解析结果为 dict）
        - 普通字符串（包装为 {"input": str}）
        - 其他对象（字符串化后包装）
        """
        if isinstance(input_text, dict):
            return input_text
        if isinstance(input_text, str):
            try:
                parsed = json.loads(input_text)
            except json.JSONDecodeError:
                return {"input": input_text}
            if isinstance(parsed, dict):
                return parsed
            return {"input": input_text}
        return {"input": str(input_text)}

    def _check_circuit(self, name: str, trace_span=None) -> Optional[ToolResponse]:
        """熔断检查：若熔断开启返回 CIRCUIT_OPEN 响应，否则返回 None"""
        if self.circuit_breaker.is_open(name):
            status = self.circuit_breaker.get_status(name)
            response = ToolResponse.error(
                code=ToolErrorCode.CIRCUIT_OPEN,
                message=f"工具 '{name}' 当前被禁用，由于连续失败。{status['recover_in_seconds']} 秒后可用。",
                context={
                    "tool_name": name,
                    "circuit_status": status
                }
            )
            self._finish_trace_span(trace_span, error=True)
            return response
        return None

    @staticmethod
    def _finish_trace_span(span, error: bool = False):
        """结束追踪 span"""
        try:
            if span is not None:
                from agentorchestra.runtime.core.telemetry.tracing import get_tracer
                if error:
                    span.set_error()
                span.set_attribute("status", "error" if error else "ok")
                get_tracer().end_span(span)
        except Exception:
            pass

    def _record_observability(self, name: str, response: ToolResponse):
        """记录熔断结果 + 指标埋点"""
        # 记录熔断器结果
        self.circuit_breaker.record_result(name, response)

        # 指标埋点
        try:
            from agentorchestra.runtime.core.telemetry.metrics import get_metrics
            metrics = get_metrics()
            metrics.record_tool_call(name, error=_is_error_response(response))
        except Exception:
            pass

    def _start_trace(self, name: str):
        """启动追踪 span"""
        try:
            from agentorchestra.runtime.core.telemetry.tracing import get_tracer
            return get_tracer().start_span(f"tool.{name}", {"tool": name})
        except Exception:
            return None

    def execute_tool(self, name: str, input_text: str) -> ToolResponse:
        """执行工具（受 contextvars 临时过滤保护）"""
        #：临时禁用的工具直接返回 NOT_FOUND，不进入熔断 / 执行路径
        if name in _disabled_tools_var.get() or name in _disabled_functions_var.get():
            return ToolResponse.error(
                code=ToolErrorCode.NOT_FOUND,
                message=f"工具 '{name}' 当前被临时禁用（子代理隔离）",
                context={"tool_name": name},
            )
        trace_span = self._start_trace(name)

        # 检查熔断器
        circuit_response = self._check_circuit(name, trace_span)
        if circuit_response is not None:
            return circuit_response

        # 解析参数
        parameters = self._parse_input(input_text)

        # 执行工具
        if name in self._tools:
            tool = self._tools[name]
            try:
                # 使用 run_with_timing 自动添加时间统计
                response = tool.run_with_timing(parameters)
            except Exception as e:
                response = ToolResponse.error(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"执行工具 '{name}' 时发生异常: {str(e)}",
                    context={"tool_name": name, "input": input_text}
                )

        # 查找函数工具（自动包装为新协议）
        elif name in self._functions:
            func = self._functions[name]["func"]
            start_time = time.time()

            try:
                result = func(parameters)
                elapsed_ms = measure_elapsed_ms(start_time)
                response = _wrap_function_response(result, elapsed_ms, name, input_text)
            except Exception as e:
                elapsed_ms = measure_elapsed_ms(start_time)
                response = ToolResponse.error(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"函数执行失败: {str(e)}",
                    stats={"time_ms": elapsed_ms},
                    context={"tool_name": name, "input": input_text}
                )

        # 工具不存在
        else:
            response = ToolResponse.error(
                code=ToolErrorCode.NOT_FOUND,
                message=f"未找到名为 '{name}' 的工具",
                context={"tool_name": name}
            )

        # 观测埋点 + 结束追踪
        self._record_observability(name, response)
        self._finish_trace_span(trace_span, error=_is_error_response(response))

        return response

    async def async_execute_tool(self, name: str, input_text: str) -> ToolResponse:
        """
        异步执行工具，返回 ToolResponse 对象（带熔断器保护）

        与 execute_tool 语义一致，供异步/流式 Agent 使用，保证
        所有工具执行路径都经过统一的熔断检查与观测埋点。

        Args:
            name: 工具名称
            input_text: 输入参数（JSON 字符串或字典）

        Returns:
            ToolResponse: 标准化的工具响应对象
        """
        trace_span = self._start_trace(name)

        # 检查熔断器
        circuit_response = self._check_circuit(name, trace_span)
        if circuit_response is not None:
            return circuit_response

        # 解析参数
        parameters = self._parse_input(input_text)

        # 执行工具
        if name in self._tools:
            tool = self._tools[name]
            try:
                response = await tool.arun_with_timing(parameters)
            except Exception as e:
                response = ToolResponse.error(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"执行工具 '{name}' 时发生异常: {str(e)}",
                    context={"tool_name": name, "input": input_text}
                )

        elif name in self._functions:
            func = self._functions[name]["func"]
            start_time = time.time()

            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, func, parameters)
                elapsed_ms = measure_elapsed_ms(start_time)
                response = _wrap_function_response(result, elapsed_ms, name, input_text)
            except Exception as e:
                elapsed_ms = measure_elapsed_ms(start_time)
                response = ToolResponse.error(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"函数执行失败: {str(e)}",
                    stats={"time_ms": elapsed_ms},
                    context={"tool_name": name, "input": input_text}
                )

        else:
            response = ToolResponse.error(
                code=ToolErrorCode.NOT_FOUND,
                message=f"未找到名为 '{name}' 的工具",
                context={"tool_name": name}
            )

        # 观测埋点 + 结束追踪
        self._record_observability(name, response)
        self._finish_trace_span(trace_span, error=_is_error_response(response))

        return response

    def get_tools_description(self) -> str:
        """
        获取所有可用工具的格式化描述字符串

        Returns:
            工具描述字符串，用于构建提示词
        """
        descriptions = []

        # Tool对象描述
        for tool in self._tools.values():
            descriptions.append(f"- {tool.name}: {tool.description}")

        # 函数工具描述
        for name, info in self._functions.items():
            descriptions.append(f"- {name}: {info['description']}")

        return "\n".join(descriptions) if descriptions else "暂无可用工具"

    def list_tools(self) -> list[str]:
        """列出所有工具名称（排除当前 context 临时禁用的）"""
        disabled = _disabled_tools_var.get() | _disabled_functions_var.get()
        all_names = list(self._tools.keys()) + list(self._functions.keys())
        return [n for n in all_names if n not in disabled]

    def get_all_tools(self) -> list[Tool]:
        """获取所有Tool对象"""
        return list(self._tools.values())

    def clear(self):
        """清空所有工具"""
        self._tools.clear()
        self._functions.clear()
        logger.info("所有工具已清空")

    # ==================== 乐观锁机制支持 ====================

    def set_read_metadata(self, file_path: str, metadata: Dict[str, Any]):
        """缓存 Read 工具获取的文件元数据

        Args:
            file_path: 文件路径（相对于 project_root）
            metadata: 文件元数据字典，包含：
                - file_mtime_ms: 文件修改时间（毫秒时间戳）
                - file_size_bytes: 文件大小（字节）
        """
        self.read_metadata_cache[file_path] = metadata

    def get_read_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """获取缓存的文件元数据

        Args:
            file_path: 文件路径

        Returns:
            文件元数据字典，如果不存在则返回 None
        """
        return self.read_metadata_cache.get(file_path)

    def clear_read_cache(self, file_path: Optional[str] = None):
        """清空文件元数据缓存

        Args:
            file_path: 指定文件路径，如果为 None 则清空所有缓存
        """
        if file_path:
            self.read_metadata_cache.pop(file_path, None)
        else:
            self.read_metadata_cache.clear()

# 全局工具注册表
global_registry = ToolRegistry()
