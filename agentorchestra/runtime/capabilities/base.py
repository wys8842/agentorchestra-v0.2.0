"""Capability 基类与上下文（Phase 2）

Capability 是 Agent 的可插拔能力单元：
- 提供 install() / uninstall() 钩子
- 通过 CapabilityContext 访问 Agent / Config / ToolRegistry 等共享资源
- 不直接修改 Agent 内部状态
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from agentorchestra.capability.tools.registry import ToolRegistry
    from agentorchestra.runtime.core.config import Config
    from agentorchestra.runtime.core.llm import SymphonyLLM


@dataclass
class CapabilityContext:
    """Capability 共享上下文（在 Agent.__init__ 中创建并传入各 capability）。

    Attributes:
        config: Agent 配置（已分组：config.trace, config.skills ...）
        llm: SymphonyLLM 实例
        tool_registry: 工具注册表（可能为 None）
        logger_name: 日志前缀（如 'agent.{name}.capability'）
        name: Agent 名称（用于 capability 标识）
        state: 任意 capability 共享状态（如 ontology_engine / checkpoint_store 等）
    """

    config: "Config"
    llm: "SymphonyLLM"
    tool_registry: Optional["ToolRegistry"]
    logger_name: str
    name: str
    state: Dict[str, Any]


class Capability(ABC):
    """Agent 可插拔能力基类。

    子类实现：
    - name: 唯一标识
    - is_enabled(ctx): 是否启用（基于 config）
    - install(ctx): 初始化（懒建依赖、注册工具、注入字段）
    - uninstall(ctx): 清理（关闭连接、保存状态）
    """

    name: str = ""

    @abstractmethod
    def is_enabled(self, ctx: CapabilityContext) -> bool:
        """判断此 capability 在当前 ctx 下是否启用。"""

    @abstractmethod
    def install(self, ctx: CapabilityContext) -> None:
        """安装 capability（创建依赖、注册工具等副作用）。"""

    def uninstall(self, ctx: CapabilityContext) -> None:  # noqa: D401
        """卸载 capability（默认 no-op；子类可重写关闭连接）。"""
        return None
