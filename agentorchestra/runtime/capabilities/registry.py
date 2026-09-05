"""CapabilityRegistry - 统一编排 Agent capability（Phase 2）

提供：
- 注册 capability（按 name 去重）
- 一键 install / uninstall
- 默认 capability 集（与 v0.1 Config 行为对齐：opt-in by default）
"""

from __future__ import annotations

import logging
from typing import Dict, List

from .base import Capability, CapabilityContext


class CapabilityRegistry:
    """Agent capability 注册表。

    使用：
        reg = CapabilityRegistry()
        reg.register(TraceCapability())
        reg.install_all(ctx)

    默认内置 capability 列表见 `default_capabilities()`：
    TraceCapability / SkillsCapability / MCPCapability / OntologyCapability /
    SessionCapability / MemoryCapability / SubAgentCapability / TodoWriteCapability /
    DevLogCapability / StateCheckpointCapability / SnapshotCapability /
    SmartCompressionCapability / ContextBuilderCapability
    """

    def __init__(self) -> None:
        self._caps: Dict[str, Capability] = {}

    def register(self, capability: Capability) -> "CapabilityRegistry":
        """注册 capability（name 重复则覆盖）。"""
        if not capability.name:
            raise ValueError(f"capability {type(capability).__name__} 必须设置 name")
        self._caps[capability.name] = capability
        return self

    def unregister(self, name: str) -> None:
        """注销 capability。"""
        self._caps.pop(name, None)

    def get(self, name: str) -> Capability:
        """按名获取 capability。"""
        if name not in self._caps:
            raise KeyError(f"capability 未注册: {name}")
        return self._caps[name]

    def list_names(self) -> List[str]:
        """已注册的 capability 名称列表。"""
        return list(self._caps.keys())

    def install_all(self, ctx: CapabilityContext) -> None:
        """按注册顺序安装所有启用 capability。

        容错：单个 capability 失败不阻断其他 capability。
        """
        logger = logging.getLogger(ctx.logger_name)
        for cap in self._caps.values():
            try:
                if cap.is_enabled(ctx):
                    cap.install(ctx)
                else:
                    logger.debug("capability %s 禁用（config 关闭或依赖缺失）", cap.name)
            except Exception as e:  # noqa: BLE001
                logger.warning("capability %s 安装失败: %s", cap.name, e)

    def uninstall_all(self, ctx: CapabilityContext) -> None:
        """逆序卸载所有 capability。"""
        for cap in reversed(list(self._caps.values())):
            try:
                cap.uninstall(ctx)
            except Exception as e:  # noqa: BLE001
                pass


def default_capabilities() -> CapabilityRegistry:
    """返回默认 capability 注册表（v0.1 标准集）。

    v0.1 起所有 capability 默认 opt-in（仅当 config 显式开启才 install）。
    """
    # 延迟导入避免循环依赖
    from agentorchestra.runtime.capabilities.builtins import (
        ContextBuilderCapability,
        DevLogCapability,
        MCPCapability,
        MemoryCapability,
        OntologyCapability,
        SessionCapability,
        SkillsCapability,
        SmartCompressionCapability,
        SnapshotCapability,
        StateCheckpointCapability,
        SubAgentCapability,
        TodoWriteCapability,
        TraceCapability,
    )

    reg = CapabilityRegistry()
    for cap_cls in (
        TraceCapability,
        SkillsCapability,
        MCPCapability,
        OntologyCapability,
        SessionCapability,
        MemoryCapability,
        SubAgentCapability,
        TodoWriteCapability,
        DevLogCapability,
        StateCheckpointCapability,
        SnapshotCapability,
        SmartCompressionCapability,
        ContextBuilderCapability,
    ):
        reg.register(cap_cls())
    return reg