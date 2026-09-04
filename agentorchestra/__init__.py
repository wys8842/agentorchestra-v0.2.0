"""
Symphony - 灵活、可扩展的多智能体框架

基于OpenAI原生API构建，提供简洁高效的智能体开发体验。

源码按领域组织（runtime / capability / governance / orchestration /
observability / ontology）。经典扁平导入路径（如 ``agentorchestra.core.llm``、
``agentorchestra.state.checkpoint``）经由 :mod:`agentorchestra._legacy`
自动映射到新物理位置，保持公共 API 不变。
"""

# 注册经典路径 -> 领域化路径的兼容导入（先于一切子包导入）
from ._legacy import install_legacy_aliases as _install_legacy_aliases

_install_legacy_aliases()
del _install_legacy_aliases

# 配置第三方库的日志级别，减少噪音
import logging

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("qdrant_client").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)

from agentorchestra.capability.tools.builtin.calculator import CalculatorTool, calculate

# 工具系统
from agentorchestra.capability.tools.registry import ToolRegistry, global_registry
from agentorchestra.runtime.agents.plan_solve_agent import PlanSolveAgent
from agentorchestra.runtime.agents.react_agent import ReActAgent
from agentorchestra.runtime.agents.reflection_agent import ReflectionAgent

# Agent实现
from agentorchestra.runtime.agents.simple_agent import SimpleAgent
from agentorchestra.runtime.core.config import Config
from agentorchestra.runtime.core.exceptions import SymphonyException

# 核心组件
from agentorchestra.runtime.core.llm import SymphonyLLM
from agentorchestra.runtime.core.message import Message

from .version import __author__, __description__, __email__, __version__

__all__ = [
    # 版本信息
    "__version__",
    "__author__",
    "__email__",
    "__description__",

    # 核心组件
    "SymphonyLLM",
    "Config",
    "Message",
    "SymphonyException",

    # Agent范式
    "SimpleAgent",
    "ReActAgent",
    "ReflectionAgent",
    "PlanSolveAgent",

    # 工具系统
    "ToolRegistry",
    "global_registry",
    "CalculatorTool",
    "calculate",
]


def __getattr__(name: str):
    """懒暴露经典顶层组件名（如 ``agentorchestra.core``）到新物理包。"""
    import importlib as _importlib

    from ._legacy import _LEGACY_TOP

    target = _LEGACY_TOP.get(name)
    if target:
        module = _importlib.import_module(target)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

