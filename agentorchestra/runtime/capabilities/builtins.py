"""内置 Capability 实现（Phase 2：拆分 agent.py）

每个 capability 独立、可测试；通过 is_enabled(config) 决定是否安装。
v0.1 起所有 capability 默认 opt-in（仅当 config 显式开启才 install）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from .base import Capability, CapabilityContext


# ---------------- Trace ----------------


class TraceCapability(Capability):
    """TraceLogger 能力（opt-in）"""

    name = "trace"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.trace.enabled)

    def install(self, ctx: CapabilityContext) -> None:
        from agentorchestra.observability import TraceLogger

        logger = TraceLogger(
            output_dir=ctx.config.trace.output_dir,
            sanitize=ctx.config.trace.sanitize,
            html_include_raw_response=ctx.config.trace.html_include_raw_response,
        )
        ctx.state["trace_logger"] = logger
        logger.log_event("session_start", {
            "agent_name": ctx.name,
            "capability": self.name,
            "config": ctx.config.model_dump(),
        })


# ---------------- Skills ----------------


class SkillsCapability(Capability):
    """Skills 知识外化（opt-in；启动时扫描 skills_dir）"""

    name = "skills"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.skills.enabled)

    def install(self, ctx: CapabilityContext) -> None:
        from agentorchestra.capability.skills import SkillLoader

        loader = SkillLoader(skills_dir=Path(ctx.config.skills.dir))
        ctx.state["skill_loader"] = loader
        # 自动注册 SkillTool
        if ctx.config.skills.auto_register and ctx.tool_registry is not None:
            from agentorchestra.capability.tools.builtin.skill_tool import SkillTool
            ctx.tool_registry.register_tool(SkillTool(skill_loader=loader))


# ---------------- MCP ----------------


class MCPCapability(Capability):
    """MCP（Model Context Protocol）集成（opt-in）"""

    name = "mcp"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.mcp.enabled) and ctx.tool_registry is not None

    def install(self, ctx: CapabilityContext) -> None:
        try:
            from agentorchestra.capability.tools.builtin.mcp_tool import MCPServerManager
            mgr = MCPServerManager(config_file=ctx.config.mcp.config_file)
            for tool in mgr.connect_all():
                if ctx.tool_registry is not None:
                    ctx.tool_registry.register_tool(tool)
        except ImportError as e:
            logging.getLogger(ctx.logger_name).warning("MCP 未启用: %s", e)


# ---------------- Ontology ----------------


class OntologyCapability(Capability):
    """企业级 Ontology 引擎（opt-in）"""

    name = "ontology"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.ontology.engine_enabled) and ctx.tool_registry is not None

    def install(self, ctx: CapabilityContext) -> None:
        from agentorchestra.ontology.engine import OntologyEngine
        from agentorchestra.ontology.governance import SecurityContext

        try:
            if ctx.config.ontology.engine_module:
                # 用户自定义装配模块
                import importlib
                module = importlib.import_module(ctx.config.ontology.engine_module)
                engine = module.build_engine()
            else:
                from agentorchestra.ontology.storage.backends import (
                    MemoryBackend,
                    SQLiteBackend,
                    BaseStorageBackend,
                )
                from agentorchestra.ontology.storage.graph_store import GraphStore
                from agentorchestra.ontology.storage.object_store import ObjectStore

                backend: BaseStorageBackend
                if ctx.config.ontology.backend == "sqlite":
                    backend = SQLiteBackend(db_path=ctx.config.ontology.db_path)
                else:
                    backend = MemoryBackend()

                engine = OntologyEngine(
                    security_ctx=SecurityContext(
                        principal=ctx.config.ontology.default_principal,
                        roles=list(ctx.config.ontology.default_roles),
                    ),
                    object_store=ObjectStore(graph=GraphStore(), backend=backend),
                )

            if ctx.tool_registry is not None:
                engine.mount(ctx.tool_registry)
            ctx.state["ontology_engine"] = engine
        except Exception as e:  # noqa: BLE001
            logging.getLogger(ctx.logger_name).warning("Ontology 引擎未启用: %s", e)


# ---------------- Session ----------------


class SessionCapability(Capability):
    """会话持久化（opt-in）"""

    name = "session"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.session.enabled)

    def install(self, ctx: CapabilityContext) -> None:
        from agentorchestra.runtime.core.session_store import SessionStore

        store = SessionStore(session_dir=ctx.config.session.dir)
        ctx.state["session_store"] = store


# ---------------- Memory ----------------


class MemoryCapability(Capability):
    """跨会话持久记忆（opt-in）"""

    name = "memory"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.memory.enabled)

    def install(self, ctx: CapabilityContext) -> None:
        try:
            from agentorchestra.capability.memory import MemoryManager

            mgr = MemoryManager.from_config(
                ctx.config,
                llm=ctx.llm,
                default_namespace=ctx.config.memory.namespace,
            )
            ctx.state["memory_manager"] = mgr
            # 自动注册工具
            if ctx.config.memory.auto_register_tools and ctx.tool_registry is not None:
                from agentorchestra.capability.memory.tools import MemoryRecallTool, MemorySaveTool
                ctx.tool_registry.register_tool(MemorySaveTool(mgr))
                ctx.tool_registry.register_tool(MemoryRecallTool(mgr))
        except Exception as e:  # noqa: BLE001
            logging.getLogger(ctx.logger_name).warning("Memory 系统未启用: %s", e)


# ---------------- SubAgent ----------------


class SubAgentCapability(Capability):
    """子代理机制（opt-in；自动注册 TaskTool）"""

    name = "subagent"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.subagent.enabled) and ctx.tool_registry is not None

    def install(self, ctx: CapabilityContext) -> None:
        from agentorchestra.capability.tools.builtin.task_tool import TaskTool
        from agentorchestra.runtime.agents.factory import default_subagent_factory

        # 工厂：使用 light LLM 或主 LLM
        def agent_factory(agent_type: str) -> Any:
            if ctx.config.subagent.use_light_llm:
                light_llm = ctx.llm.__class__(
                    provider=ctx.config.subagent.light_llm_provider,
                    model=ctx.config.subagent.light_llm_model,
                )
            else:
                light_llm = ctx.llm
            return default_subagent_factory(
                agent_type=agent_type,
                llm=light_llm,
                tool_registry=ctx.tool_registry,
                config=ctx.config,
            )

        ctx.tool_registry.register_tool(
            TaskTool(
                agent_factory=agent_factory,
                tool_registry=ctx.tool_registry,
                config=ctx.config,
            )
        )


# ---------------- TodoWrite ----------------


class TodoWriteCapability(Capability):
    """TodoWrite 进度管理（opt-in）"""

    name = "todowrite"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.todowrite.enabled) and ctx.tool_registry is not None

    def install(self, ctx: CapabilityContext) -> None:
        from agentorchestra.capability.tools.builtin.todowrite_tool import TodoWriteTool

        ctx.tool_registry.register_tool(
            TodoWriteTool(
                project_root=".",
                persistence_dir=ctx.config.todowrite.persistence_dir,
            )
        )


# ---------------- DevLog ----------------


class DevLogCapability(Capability):
    """DevLog 开发日志（opt-in）"""

    name = "devlog"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.devlog.enabled) and ctx.tool_registry is not None

    def install(self, ctx: CapabilityContext) -> None:
        from agentorchestra.runtime.core.utils import generate_session_id
        from agentorchestra.capability.tools.builtin.devlog_tool import DevLogTool

        session_id = (
            ctx.state.get("trace_logger")
            and getattr(ctx.state["trace_logger"], "session_id", generate_session_id())
        ) or generate_session_id()

        ctx.tool_registry.register_tool(
            DevLogTool(
                session_id=session_id,
                agent_name=ctx.name,
                project_root=".",
                persistence_dir=ctx.config.devlog.persistence_dir,
            )
        )


# ---------------- StateCheckpoint ----------------


class StateCheckpointCapability(Capability):
    """M0 durable checkpoint（opt-in）"""

    name = "state_checkpoint"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.state_checkpoint.enabled)

    def install(self, ctx: CapabilityContext) -> None:
        try:
            from agentorchestra.orchestration.state import get_default_store
            from agentorchestra.orchestration.state.thread import ThreadManager

            mode = (ctx.config.state_checkpoint.persistence_mode or "sqlite").lower()
            url = (ctx.config.state_checkpoint.db_url or "").strip()
            if mode == "in_memory":
                store = get_default_store("in_memory://")
            elif mode == "postgres":
                if not url:
                    raise ValueError("persistence_mode='postgres' 需要 db_url")
                store = get_default_store(url)
            else:
                store = get_default_store(url) if url else get_default_store()

            ctx.state["checkpoint_store"] = store
            ctx.state["thread_manager"] = ThreadManager(store=store)

            # 桥接 ontology object_store 的 WAL
            engine = ctx.state.get("ontology_engine")
            if engine is not None:
                obj_store = getattr(engine, "object_store", None)
                if obj_store is not None:
                    obj_store.set_wal_thread_id(None)

            # 后台 snapshot worker（可选）
            if ctx.config.state_checkpoint.wal_snapshot_enabled:
                from agentorchestra.orchestration.state.snapshot import SnapshotPolicy, SnapshotWorker
                ctx.state["snapshot_worker"] = SnapshotWorker(
                    store=store,
                    policy=SnapshotPolicy(
                        wal_threshold=ctx.config.state_checkpoint.wal_snapshot_threshold,
                        interval_seconds=ctx.config.state_checkpoint.wal_snapshot_interval_seconds,
                        enabled=True,
                    ),
                )
        except Exception as e:  # noqa: BLE001
            logging.getLogger(ctx.logger_name).warning("Checkpoint store 未启用: %s", e)


# ---------------- Snapshot ----------------


class SnapshotCapability(Capability):
    """快照 worker（默认依赖 StateCheckpointCapability 已注入 store）"""

    name = "snapshot"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return (
            ctx.config.state_checkpoint.enabled
            and ctx.config.state_checkpoint.wal_snapshot_enabled
            and ctx.state.get("checkpoint_store") is not None
        )

    def install(self, ctx: CapabilityContext) -> None:
        # 实际 SnapshotWorker 由 StateCheckpointCapability 创建并放入 ctx.state
        pass


# ---------------- SmartCompression ----------------


class SmartCompressionCapability(Capability):
    """智能摘要（opt-in；隐式 LLM 成本）"""

    name = "smart_compression"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.smart_compression.enabled)

    def install(self, ctx: CapabilityContext) -> None:
        ctx.state["enable_smart_compression"] = True


# ---------------- ContextBuilder ----------------


class ContextBuilderCapability(Capability):
    """GSSC 上下文构建器（opt-in；依赖 tiktoken）"""

    name = "context_builder"

    def is_enabled(self, ctx: CapabilityContext) -> bool:
        return bool(ctx.config.context_builder.enabled)

    def install(self, ctx: CapabilityContext) -> None:
        try:
            from agentorchestra.runtime.context.builder import ContextBuilder, ContextConfig

            ctx.state["context_builder"] = ContextBuilder(
                config=ContextConfig(max_tokens=ctx.config.context_builder.max_tokens)
            )
        except Exception as e:  # noqa: BLE001
            logging.getLogger(ctx.logger_name).warning("GSSC 上下文构建器未启用: %s", e)