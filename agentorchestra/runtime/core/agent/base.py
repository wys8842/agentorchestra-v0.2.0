"""Agent基类"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Dict, List, Optional

from agentorchestra.runtime.core.agent.lifecycle import AgentEvent, EventType, LifecycleHook
from agentorchestra.runtime.core.config import Config
from agentorchestra.runtime.core.llm import SymphonyLLM
from agentorchestra.runtime.core.message import Message
from agentorchestra.runtime.core.telemetry.logging import get_logger
from agentorchestra.runtime.core.utils import duration_seconds, truncate_text

if TYPE_CHECKING:
    from agentorchestra.capability.tools.registry import ToolRegistry
    from agentorchestra.capability.tools.tool_filter import BaseToolFilter


class Agent(ABC):
    """Agent基类

    集成能力：
    - HistoryManager: 历史管理与压缩
    - ObservationTruncator: 工具输出截断
    - TraceLogger: 可观测性（JSONL + HTML）
    - ToolRegistry: 工具管理（可选）
    - SkillLoader: 知识外化（可选）

    向后兼容：
    - self._history 属性仍然可用（通过 property 代理）
    - add_message/clear_history/get_history 方法保持不变
    """

    logger: logging.Logger  # Type annotation for logger
    max_steps: Optional[int] = None  # May be set by subclasses

    def __init__(
        self,
        name: str,
        llm: SymphonyLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        tool_registry: Optional['ToolRegistry'] = None
    ):
        self.name = name
        self.llm = llm
        self._system_prompt_base = system_prompt
        self.config = config or Config()

        # 工具注册表（可选）
        self.tool_registry = tool_registry

        # 注册配置变更回调
        self._register_config_callback()

        #上下文工程组件
        from agentorchestra.runtime.context.history import HistoryManager
        from agentorchestra.runtime.context.truncator import ObservationTruncator

        self.history_manager = HistoryManager(
            min_retain_rounds=self.config.min_retain_rounds,
            compression_threshold=self.config.compression_threshold
        )

        self.truncator = ObservationTruncator(
            max_lines=self.config.tool_output_max_lines,
            max_bytes=self.config.tool_output_max_bytes,
            truncate_direction=self.config.tool_output_truncate_direction,
            output_dir=self.config.tool_output_dir
        )

        #Token 计数器（缓存 + 增量计算）
        from agentorchestra.runtime.context.token_counter import TokenCounter
        model_name = self.llm.model
        if model_name is None:
            model_name = "unknown"
        self.token_counter = TokenCounter(model=model_name)
        self._history_token_count = 0  # 缓存历史 Token 数

        #GSSC 上下文构建器（可选，融合多路信息到上下文）
        # 通过 context_builder_enabled 开启；依赖 tiktoken（可选安装）
        self.context_builder: Optional[Any] = None
        if self.config.context_builder_enabled:
            try:
                from agentorchestra.runtime.context.builder import ContextBuilder, ContextConfig
                self.context_builder = ContextBuilder(
                    config=ContextConfig(max_tokens=self.config.context_builder_max_tokens)
                )
            except ImportError as e:
                if self.config.debug:
                    self.logger.warning(f"GSSC 上下文构建器未启用: {e}")
                self.context_builder = None
            except Exception as e:
                self.logger.warning(f"GSSC 上下文构建器初始化失败: {e}")
                self.context_builder = None

        # ---------------- Phase 2：能力下放至 capabilities/builtins.py ----------------
        # 原本散落在此的 TraceLogger / SkillLoader / MCP / Ontology / SessionStore /
        # MemoryManager / TaskTool / TodoWriteTool / DevLogTool / Checkpoint 初始化
        # 已全部迁移至 `agentorchestra.runtime.capabilities` 子包（13 个内置 Capability）。
        # 此处仅保留会话元数据与 capability 编排钩子。

        # 日志器（核心基础设施，所有 capability 共享）
        self.logger = get_logger("core.agent")

        # 会话元数据（用于 session_store.save() 的 metadata 字段）
        from datetime import datetime
        self._session_metadata = {
            "created_at": datetime.now().isoformat(),
            "total_tokens": 0,
            "total_steps": 0,
            "duration_seconds": 0,
        }
        self._start_time = datetime.now()

        # 记忆注入前缀（运行时填充；memory capability 自动 recall 后写入）
        self._memory_inject_prefix: str = ""
        self.memory_namespace: str = getattr(self.config, "memory_namespace", "default") or "default"

        # Checkpoint 状态字段（M0，即使 capability 未启用也要保留）
        self._active_thread_id: Optional[str] = None
        self._pending_resume_response: Optional[Dict[str, Any]] = None
        self._wal_flush_target: Optional[Any] = None  # ontology object_store
        self._thread_manager: Optional[Any] = None
        self._snapshot_worker: Optional[Any] = None

        # M4：子 Agent 并发信号量（懒建；Config.max_concurrent_subagents）
        self._subagent_semaphore = None

        # ---------------- Phase 2：Capability 编排 ----------------
        # 所有 feature flag → capability 化安装；业务逻辑下沉到 capabilities/builtins.py
        # 向后兼容：capability 安装后回填 self.{trace_logger, skill_loader, ...} 属性，
        # 旧代码 `agent.memory_manager` / `agent.trace_logger` 仍可访问。
        from agentorchestra.runtime.capabilities import CapabilityContext
        from agentorchestra.runtime.capabilities.registry import default_capabilities

        self.capabilities = default_capabilities()
        self._capability_state: Dict[str, Any] = {}

        ctx = CapabilityContext(
            config=self.config,
            llm=self.llm,
            tool_registry=self.tool_registry,
            logger_name=f"agent.{name}",
            name=name,
            state=self._capability_state,
        )
        self.capabilities.install_all(ctx)

        # capability 安装结果回填到 self 属性（向后兼容）
        self.trace_logger: Optional[Any] = self._capability_state.get("trace_logger")
        self.skill_loader: Optional[Any] = self._capability_state.get("skill_loader")
        self.ontology_engine: Optional[Any] = self._capability_state.get("ontology_engine")
        self.session_store: Optional[Any] = self._capability_state.get("session_store")
        self.memory_manager: Optional[Any] = self._capability_state.get("memory_manager")
        self.checkpoint_store: Optional[Any] = self._capability_state.get("checkpoint_store")
        self._thread_manager = self._capability_state.get("thread_manager")
        self._snapshot_worker = self._capability_state.get("snapshot_worker")
        self.context_builder: Optional[Any] = self._capability_state.get("context_builder")

    def _wire_ontology_wal(self, obj_store: Any) -> None:
        """把 ObjectStore 同步 WAL queue 桥接到 CheckpointStore（async）。"""
        self._wal_flush_target = obj_store
        # 设置 thread_id 为空（运行时由 Agent 设置）
        obj_store.set_wal_thread_id(None)

    @property
    def system_prompt(self) -> str:
        """返回含记忆前缀的最终 system_prompt（向后兼容：子类直接读取 self.system_prompt）。"""
        base = self._system_prompt_base or ""
        prefix = getattr(self, "_memory_inject_prefix", "") or ""
        if not prefix:
            return base
        return (prefix + "\n\n" + base).strip() if base else prefix

    @system_prompt.setter
    def system_prompt(self, value: Optional[str]) -> None:
        """设置基础 system_prompt（不含记忆前缀，由 getter 负责拼接）"""
        self._system_prompt_base = value

    @property
    def _history(self) -> List[Message]:
        """向后兼容：通过 property 代理到 HistoryManager"""
        return self.history_manager.get_history()

    @_history.setter
    def _history(self, value: List[Message]):
        """向后兼容：允许直接设置历史"""
        self.history_manager.clear()
        for msg in value:
            self.history_manager.append(msg)

    @property
    def working_dir(self) -> str:
        """工作目录（子类可覆盖）"""
        return "."

    @abstractmethod
    def run(self, input_text: str, **kwargs) -> str:
        """运行Agent（同步版本）"""
        pass

    # ==================== 异步生命周期方法 ====================

    async def arun(
        self,
        input_text: str,
        on_start: LifecycleHook = None,
        on_step: LifecycleHook = None,
        on_finish: LifecycleHook = None,
        on_error: LifecycleHook = None,
        **kwargs
    ) -> str:
        """
        异步执行 Agent（基础版本）

        默认实现：在线程池中运行同步 run() 方法
        子类可以覆盖此方法实现更复杂的异步逻辑（如工具并行）

        Args:
            input_text: 输入文本
            on_start: Agent 开始执行时的钩子
            on_step: 每个推理步骤的钩子
            on_finish: Agent 执行完成时的钩子
            on_error: 发生错误时的钩子
            **kwargs: 其他参数

        Returns:
            执行结果

        Example:
            >>> agent = SimpleAgent(...)
            >>> result = await agent.arun("Hello", on_start=my_hook)
        """
        # 触发开始事件
        await self._emit_event(
            EventType.AGENT_START,
            on_start,
            input_text=input_text
        )

        # 记忆自动注入：把相关历史记忆拼到 system_prompt 之后
        self._memory_inject_prefix = ""
        if (
            self.memory_manager
            and self.config.memory_auto_recall
            and input_text
        ):
            try:
                recalled = self.memory_manager.recall(
                    input_text,
                    top_k=self.config.memory_recall_top_k,
                    namespace=self.memory_namespace,
                )
                if recalled:
                    self._memory_inject_prefix = self._format_memory_prefix(recalled)
            except (TypeError, AttributeError) as e:
                self.logger.warning(f"记忆回忆失败: {e}")

        try:
            # 默认实现：在线程池中运行同步 run()
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.run(input_text, **kwargs)
            )

            # 触发完成事件
            await self._emit_event(
                EventType.AGENT_FINISH,
                on_finish,
                result=result
            )

            # 记忆自动总结（默认关闭）
            if (
                self.memory_manager
                and self.config.memory_auto_summarize
            ):
                try:
                    await self._auto_memorize(input_text, result)
                except Exception as e:
                    self.logger.warning(f"自动总结失败: {e}")

            return result

        except Exception as e:
            # 触发错误事件
            await self._emit_event(
                EventType.AGENT_ERROR,
                on_error,
                error=str(e),
                error_type=type(e).__name__
            )
            raise

    async def arun_stream(
        self,
        input_text: str,
        **kwargs
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        流式执行 Agent（基础版本）

        默认实现：执行 arun() 并返回开始/完成事件
        子类应该覆盖此方法实现真正的流式输出

        Args:
            input_text: 输入文本
            **kwargs: 其他参数

        Yields:
            AgentEvent: 生命周期事件

        Example:
            >>> async for event in agent.arun_stream("Hello"):
            ...     print(event.type, event.data)
        """
        # 开始事件
        yield AgentEvent.create(
            EventType.AGENT_START,
            self.name,
            input_text=input_text
        )

        # 执行
        try:
            result = await self.arun(input_text, **kwargs)

            # 完成事件
            yield AgentEvent.create(
                EventType.AGENT_FINISH,
                self.name,
                result=result
            )
        except Exception as e:
            # 错误事件
            yield AgentEvent.create(
                EventType.AGENT_ERROR,
                self.name,
                error=str(e),
                error_type=type(e).__name__
            )
            raise

    async def _emit_event(
        self,
        event_type: EventType,
        hook: LifecycleHook,
        **data
    ):
        """触发事件并调用钩子

        Args:
            event_type: 事件类型
            hook: 生命周期钩子（可选）
            **data: 事件数据
        """
        event = AgentEvent.create(event_type, self.name, **data)

        if hook:
            try:
                # 使用 asyncio.wait_for 设置超时
                timeout = getattr(self.config, 'hook_timeout_seconds', 5.0)
                await asyncio.wait_for(hook(event), timeout=timeout)
            except asyncio.TimeoutError:
                # 钩子超时不应中断主流程
                if hasattr(self, 'trace_logger') and self.trace_logger:
                    self.trace_logger.log_event(
                        "hook_timeout",
                        {"event_type": event_type.value, "timeout": timeout}
                    )
            except Exception as e:
                # 钩子异常不应中断主流程
                if hasattr(self, 'trace_logger') and self.trace_logger:
                    self.trace_logger.log_event(
                        "hook_error",
                        {"event_type": event_type.value, "error": str(e)}
                    )

    # ==================== 记忆辅助方法 ====================

    def _format_memory_prefix(self, entries: List[Any]) -> str:
        """把 recall 命中的记忆拼成固定前缀（注入到 system_prompt 之后）。"""
        if not entries:
            return ""
        lines = ["以下是与该任务相关的过往记忆（来自历史会话）："]
        for i, e in enumerate(entries, 1):
            type_val = e.type.value if hasattr(e.type, "value") else str(e.type)
            tags = ", ".join(e.tags) if e.tags else "-"
            lines.append(
                f"- [{type_val}] (importance={e.importance:.1f}, tags=[{tags}]) {e.content}"
            )
        lines.append("若与当前任务无关可忽略。")
        return "\n".join(lines)

    def _effective_system_prompt(self) -> str:
        """返回含记忆前缀的最终 system_prompt。"""
        prefix = self._memory_inject_prefix or ""
        base = self.system_prompt or ""
        if not prefix:
            return base
        return (prefix + "\n\n" + base).strip() if base else prefix

    async def _auto_memorize(self, input_text: str, result: str) -> None:
        """调 Summarizer 提炼本轮记忆并入库（仅 memory_auto_summarize=True 时触发）。"""
        from agentorchestra.capability.memory.summarizer import Summarizer

        if not self.memory_manager:
            return
        try:
            history = self.history_manager.get_history() if self.history_manager else []
            summarizer = Summarizer(llm=self.llm)
            candidates = await summarizer.extract(input_text, history, result)
            if not candidates:
                return
            ids = self.memory_manager.remember_batch(
                [
                    {
                        "content": c.content,
                        "type": c.type,
                        "tags": c.tags,
                        "importance": c.importance,
                    }
                    for c in candidates
                ]
            )
            if ids:
                self.logger.info(f"自动总结写入 {len(ids)} 条记忆")
        except Exception as e:
            self.logger.warning(f"自动记忆写入失败: {e}")

    def add_message(self, message: Message):
        """添加消息到历史记录

        自动检查是否需要压缩历史
        """
        self.history_manager.append(message)

        # 增量更新 Token 计数
        new_tokens = self.token_counter.count_message(message)
        self._history_token_count += new_tokens

        # 检查是否需要压缩
        if self._should_compress():
            self._compress_history()

        # 自动保存（如果启用）
        if self.config.auto_save_enabled and self.session_store:
            history_len = len(self.history_manager.get_history())
            if history_len % self.config.auto_save_interval == 0:
                self._auto_save()

    def _register_config_callback(self) -> None:
        """注册配置变更回调（自动响应）"""
        try:
            from agentorchestra.components import Components

            def _on_config_change(old, new):
                self.config = new
                # 更新并发配置
                if self.tool_registry and hasattr(self, 'truncator'):
                    try:
                        self.truncator.max_lines = new.tool_output.max_lines
                        self.truncator.max_bytes = new.tool_output.max_bytes
                    except (AttributeError, TypeError):
                        pass

            Components.on_config_change(_on_config_change)
        except ImportError:
            pass

    def clear_history(self):
        """清空历史记录"""
        self.history_manager.clear()
        # 重置 Token 计数
        self._history_token_count = 0
        self.token_counter.clear_cache()

    def get_history(self) -> List[Message]:
        """获取历史记录"""
        return self.history_manager.get_history()

    def _should_compress(self) -> bool:
        """判断是否需要压缩历史

        基于缓存的 Token 数判断（高性能）
        使用增量计算，避免重复遍历历史

        Returns:
            是否需要压缩
        """
        threshold = int(self.config.context_window * self.config.compression_threshold)
        return self._history_token_count > threshold

    def _compress_history(self):
        """压缩历史

        默认使用简单摘要策略
        如果启用 enable_smart_compression，子类可以重写此方法调用 LLM 生成智能摘要
        """
        history = self.history_manager.get_history()

        if self.config.enable_smart_compression:
            # 智能摘要（需要子类实现）
            summary = self._generate_smart_summary(history)
        else:
            # 简单摘要
            summary = self._generate_simple_summary(history)

        self.history_manager.compress(summary)

        # 重新计算 Token 数（压缩后）
        new_history = self.history_manager.get_history()
        self._history_token_count = self.token_counter.count_messages(new_history)

    def _generate_simple_summary(self, history: List[Message]) -> str:
        """生成简单摘要（统计信息）

        Args:
            history: 历史消息列表

        Returns:
            摘要文本
        """
        rounds = self.history_manager.estimate_rounds()
        user_msgs = sum(1 for msg in history if msg.role == "user")
        assistant_msgs = sum(1 for msg in history if msg.role == "assistant")

        return f"""此会话包含 {rounds} 轮对话：
            - 用户消息：{user_msgs} 条
            - 助手消息：{assistant_msgs} 条
            - 总消息数：{len(history)} 条

            （历史已压缩，保留最近 {self.config.min_retain_rounds} 轮完整对话）"""

    def _generate_smart_summary(self, history: List[Message]) -> str:
        """生成智能摘要（调用 LLM）

        使用轻量 LLM 生成结构化摘要，保留关键信息：
        - 任务目标
        - 关键决策
        - 已完成工作
        - 待处理事项
        - 重要发现

        Args:
            history: 历史消息列表

        Returns:
            摘要文本
        """
        # 1. 提取要压缩的历史片段
        boundaries = self.history_manager.find_round_boundaries()
        if len(boundaries) <= self.config.min_retain_rounds:
            return self._generate_simple_summary(history)

        # 保留最近 N 轮，压缩之前的
        keep_from_index = boundaries[-self.config.min_retain_rounds]
        to_compress = history[:keep_from_index]

        if not to_compress:
            return self._generate_simple_summary(history)

        # 2. 构建摘要 Prompt
        history_text = self._format_history_for_summary(to_compress)

        summary_prompt = f"""请将以下对话历史压缩为结构化摘要，保留关键信息：

            ## 对话历史
            {history_text}

            ## 摘要要求
            1. **任务目标**：用户想要完成什么？
            2. **关键决策**：做了哪些重要决定？
            3. **已完成工作**：完成了哪些任务？（列表形式）
            4. **待处理事项**：还有什么未完成？
            5. **重要发现**：有哪些关键信息或问题？

            请用简洁的中文输出，每部分不超过 3 行。"""

        # 3. 调用轻量 LLM（节省成本）
        try:
            summary_llm = self._get_summary_llm()

            messages = [
                {"role": "system", "content": "你是一个专业的对话摘要助手，擅长提取关键信息。"},
                {"role": "user", "content": summary_prompt}
            ]

            # 非流式调用，快速获取结果
            summary = summary_llm.invoke(
                messages,
                temperature=self.config.summary_temperature,
                max_tokens=self.config.summary_max_tokens
            )

            return f"""## 历史摘要（{len(to_compress)} 条消息）
                {summary}

                ---
                （已压缩，保留最近 {self.config.min_retain_rounds} 轮完整对话）"""

        except Exception as e:
            self.logger.warning(f"智能摘要生成失败: {e}，使用简单摘要")
            return self._generate_simple_summary(history)

    def _format_history_for_summary(self, history: List[Message]) -> str:
        """格式化历史消息用于摘要生成

        Args:
            history: 历史消息列表

        Returns:
            格式化后的历史文本
        """
        formatted_lines = []
        for msg in history:
            # 截断过长消息（避免摘要 Prompt 过大）
            content = truncate_text(msg.content, 500, ellipsis=False)
            formatted_lines.append(f"[{msg.role}]: {content}")

        return "\n\n".join(formatted_lines)

    def _get_summary_llm(self):
        """获取摘要专用 LLM（轻量模型）

        使用独立的轻量 LLM 实例，节省成本

        Returns:
            SymphonyLLM 实例
        """
        if not hasattr(self, '_summary_llm'):
            from agentorchestra.runtime.core.llm import SymphonyLLM

            # 使用配置中的轻量模型
            provider = self.config.summary_llm_provider
            model = self.config.summary_llm_model

            self._summary_llm = SymphonyLLM(
                provider=provider,
                model=model,
                temperature=self.config.summary_temperature,
                max_tokens=self.config.summary_max_tokens
            )

        return self._summary_llm

    def __str__(self) -> str:
        return f"Agent(name={self.name}, model={self.llm.model})"

    def __repr__(self) -> str:
        return self.__str__()

    # ==================== 工具调用通用能力（从 FunctionCallAgent 提取）====================

    def _build_context(self, input_text: str, system_instructions: Optional[str] = None,
                       history: Optional[List[Message]] = None,
                       additional_packets: Optional[List[Any]] = None) -> Optional[str]:
        """使用 GSSC 上下文构建器融合多路信息

        当 context_builder_enabled 开启时，将系统指令、对话历史、用户问题、
        以及调用方提供的额外信息包（知识图谱检索、工具结果等）融合为结构化上下文。
        返回 None 表示未启用或构建失败（调用方应回退到默认行为）。

        Args:
            input_text: 用户问题
            system_instructions: 系统指令（可选）
            history: 对话历史（可选）
            additional_packets: 额外上下文包（可选）

        Returns:
            融合后的上下文文本，或 None
        """
        if getattr(self, 'context_builder', None) is None:
            return None

        try:
            packets = []
            for p in (additional_packets or []):
                packets.append(p)
            # mypy doesn't understand the above None check
            builder = self.context_builder
            assert builder is not None
            return builder.build(
                user_query=input_text,
                conversation_history=history or [],
                system_instructions=system_instructions or self.system_prompt,
                additional_packets=packets
            )
        except Exception as e:
            if self.config.debug:
                self.logger.debug(f"GSSC 上下文构建失败: {e}")
            return None

    @classmethod
    def _build_tool_schemas(cls, tool_registry: Optional['ToolRegistry'] = None) -> List[Dict[str, Any]]:
        """构建工具 JSON Schema

        统一的工具 schema 构建逻辑，支持：
        - Tool 对象（带参数定义）
        - 函数工具（简化注册）
        - 无 agent 实例化（classmethod），供模块级函数（PlanSolveAgent 兼容层）调用

        Returns:
            工具 schema 列表
        """
        #
        registry = tool_registry
        if registry is None:
            registry = getattr(cls, "tool_registry", None) if hasattr(cls, "tool_registry") else None
        if registry is None:
            return []

        schemas: List[Dict[str, Any]] = []

        # 1. 处理 Tool 对象
        for tool in registry.get_all_tools():
            properties: Dict[str, Any] = {}
            required: List[str] = []

            try:
                parameters = tool.get_parameters()
            except (AttributeError, TypeError):
                parameters = []

            for param in parameters:
                properties[param.name] = {
                    "type": cls._map_parameter_type(param.type),
                    "description": param.description or ""
                }
                if param.default is not None:
                    properties[param.name]["default"] = param.default
                if getattr(param, "required", True):
                    required.append(param.name)

            schema: Dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": {
                        "type": "object",
                        "properties": properties
                    }
                }
            }
            if required:
                schema["function"]["parameters"]["required"] = required
            schemas.append(schema)

        # 2. 处理函数工具
        function_map = getattr(registry, "_functions", {})
        for name, info in function_map.items():
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": info.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "input": {
                                "type": "string",
                                "description": "输入文本"
                            }
                        },
                        "required": ["input"]
                    }
                }
            })

        return schemas

    @staticmethod
    def _map_parameter_type(param_type: str) -> str:
        """将工具参数类型映射为 JSON Schema 允许的类型

        Args:
            param_type: 工具参数类型

        Returns:
            JSON Schema 类型
        """
        normalized = (param_type or "").lower()
        if normalized in {"string", "number", "integer", "boolean", "array", "object"}:
            return normalized
        return "string"

    def _convert_parameter_types(self, tool_name: str, param_dict: Dict[str, Any]) -> Dict[str, Any]:
        """根据工具定义转换参数类型

        Args:
            tool_name: 工具名称
            param_dict: 参数字典

        Returns:
            类型转换后的参数字典
        """
        if not self.tool_registry:
            return param_dict

        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            return param_dict

        try:
            tool_params = tool.get_parameters()
        except Exception:
            return param_dict

        type_mapping = {param.name: param.type for param in tool_params}
        converted: Dict[str, Any] = {}

        for key, value in param_dict.items():
            param_type = type_mapping.get(key)
            if not param_type:
                converted[key] = value
                continue

            try:
                normalized = param_type.lower()
                if normalized in {"number", "float"}:
                    converted[key] = float(value)
                elif normalized in {"integer", "int"}:
                    converted[key] = int(value)
                elif normalized in {"boolean", "bool"}:
                    if isinstance(value, bool):
                        converted[key] = value
                    elif isinstance(value, (int, float)):
                        converted[key] = bool(value)
                    elif isinstance(value, str):
                        converted[key] = value.lower() in {"true", "1", "yes"}
                    else:
                        converted[key] = bool(value)
                else:
                    converted[key] = value
            except (TypeError, ValueError):
                converted[key] = value

        return converted

    def _execute_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """执行工具调用并返回字符串结果

        统一的工具执行逻辑（收敛到 ToolRegistry 单一入口）：
        - 熔断检查、观测埋点、结果记录统一由 registry.execute_tool 完成
        - 本方法只负责参数类型转换与响应格式化

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果（字符串格式）
        """
        if not self.tool_registry:
            return "❌ 错误：未配置工具注册表"

        # 参数类型转换（Tool 对象路径）
        tool = self.tool_registry.get_tool(tool_name)
        if tool is not None:
            try:
                arguments = self._convert_parameter_types(tool_name, arguments)
            except Exception:
                pass

        # 统一走 registry 执行（含熔断/观测/记录）
        import json
        try:
            input_text = json.dumps(arguments)
            response = self.tool_registry.execute_tool(tool_name, input_text)
        except Exception as exc:
            return f"❌ 工具调用失败：{exc}"

        # 根据状态添加前缀
        from agentorchestra.capability.tools.response import ToolStatus
        if response.status == ToolStatus.ERROR:
            error_code = response.error_info.get("code", "UNKNOWN") if response.error_info else "UNKNOWN"
            return f"❌ 错误 [{error_code}]: {response.text}"
        elif response.status == ToolStatus.PARTIAL:
            return f"⚠️ 部分成功: {response.text}"
        else:
            return response.text

    def _execute_single_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_call_id: str,
        step: int
    ) -> Dict[str, Any]:
        """执行单个工具调用并返回标准化的消息字典

        统一的工具执行流程（收敛 6 处重复逻辑）：
        1. 执行工具（_execute_tool_call）
        2. 截断超长输出（truncator）
        3. 记录工具结果到 trace_logger
        4. 构建工具消息字典

        Args:
            tool_name: 工具名称
            arguments: 工具参数
            tool_call_id: 工具调用 ID
            step: 当前步骤

        Returns:
            包含 role='tool' 的消息字典，可直接 append 到 messages
        """
        result = self._execute_tool_call(tool_name, arguments)

        if self.truncator:
            try:
                truncate_result = self.truncator.truncate(tool_name=tool_name, output=result)
                result = truncate_result.get('preview', result)
            except Exception:
                pass

        if self.trace_logger:
            self.trace_logger.log_event(
                "tool_result",
                {
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "result": result
                },
                step=step
            )

        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result
        }

    # ==================== 会话持久化能力 ====================

    def _auto_save(self):
        """自动保存会话（静默失败）"""
        if not self.session_store:
            return

        try:
            self.session_store.save(
                agent_config=self._get_agent_config(),
                history=self.history_manager.get_history(),
                tool_schema_hash=self._compute_tool_schema_hash(),
                read_cache=self._get_read_cache(),
                metadata=self._session_metadata,
                session_name="session-auto"
            )
        except Exception as e:
            if self.config.debug:
                self.logger.debug(f"自动保存失败: {e}")

    def save_session(self, session_name: str) -> str:
        """手动保存会话

        Args:
            session_name: 会话名称（不含 .json 后缀）

        Returns:
            保存的文件路径

        Raises:
            RuntimeError: 会话持久化未启用
        """
        if not self.session_store:
            raise RuntimeError("会话持久化未启用，请在 Config 中设置 session_enabled=True")

        # 更新元数据
        self._session_metadata["duration_seconds"] = duration_seconds(self._start_time)

        filepath = self.session_store.save(
            agent_config=self._get_agent_config(),
            history=self.history_manager.get_history(),
            tool_schema_hash=self._compute_tool_schema_hash(),
            read_cache=self._get_read_cache(),
            metadata=self._session_metadata,
            session_name=session_name
        )

        return filepath

    def load_session(self, filepath: str, check_consistency: bool = True) -> None:
        """加载会话

        Args:
            filepath: 会话文件路径
            check_consistency: 是否检查环境一致性

        Raises:
            RuntimeError: 会话持久化未启用
            FileNotFoundError: 文件不存在
        """
        if not self.session_store:
            raise RuntimeError("会话持久化未启用，请在 Config 中设置 session_enabled=True")

        # 加载会话数据
        session_data = self.session_store.load(filepath)

        # 环境一致性检查
        if check_consistency:
            # 检查配置一致性
            config_check = self.session_store.check_config_consistency(
                saved_config=session_data.get("agent_config", {}),
                current_config=self._get_agent_config()
            )

            if not config_check["consistent"]:
                self.logger.warning("环境配置不一致：")
                for warning in config_check["warnings"]:
                    self.logger.warning(f"  - {warning}")

            # 检查工具 Schema 一致性
            tool_check = self.session_store.check_tool_schema_consistency(
                saved_hash=session_data.get("tool_schema_hash", ""),
                current_hash=self._compute_tool_schema_hash()
            )

            if tool_check["changed"]:
                self.logger.warning("工具定义已变化")
                self.logger.warning(f"  建议：{tool_check['recommendation']}")

        # 恢复历史
        from agentorchestra.runtime.core.message import Message
        self.history_manager.clear()
        for msg_data in session_data.get("history", []):
            self.history_manager.append(Message.from_dict(msg_data))

        # 恢复元数据
        self._session_metadata = session_data.get("metadata", {})

        # 恢复 Read 工具缓存
        if self.tool_registry and session_data.get("read_cache"):
            self.tool_registry.read_metadata_cache = session_data["read_cache"]

        self.logger.info(f"会话已恢复：{session_data.get('session_id', 'unknown')}")

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有可用会话

        Returns:
            会话信息列表
        """
        if not self.session_store:
            return []

        return self.session_store.list_sessions()

    def _get_agent_config(self) -> Dict[str, Any]:
        """获取 Agent 配置信息

        Returns:
            配置字典
        """
        config = {
            "name": self.name,
            "agent_type": self.__class__.__name__,
            "llm_provider": getattr(self.llm, 'provider', 'unknown'),
            "llm_model": getattr(self.llm, 'model_id', getattr(self.llm, 'model', 'unknown'))
        }

        # 添加 max_steps（如果存在）
        if hasattr(self, 'max_steps'):
            config["max_steps"] = self.max_steps

        return config

    def _compute_tool_schema_hash(self) -> str:
        """计算工具 Schema 哈希

        用于检测工具定义是否变化

        Returns:
            工具 Schema 哈希值（16位）
        """
        if not self.tool_registry:
            return "no-tools"

        import json
        from hashlib import sha256

        # 收集所有工具的签名
        tools_signature = {}
        for tool_name in sorted(self.tool_registry.list_tools()):
            tool = self.tool_registry.get_tool(tool_name)
            if tool:
                tools_signature[tool_name] = {
                    "name": tool.name,
                    "description": tool.description[:100] if tool.description else "",
                    "parameters": list(tool.parameters.keys()) if hasattr(tool, 'parameters') and tool.parameters else []
                }

        schema_str = json.dumps(tools_signature, sort_keys=True)
        return sha256(schema_str.encode()).hexdigest()[:16]

    def _get_read_cache(self) -> Dict[str, Dict]:
        """获取 Read 工具的元数据缓存

        Returns:
            元数据缓存字典
        """
        if self.tool_registry and hasattr(self.tool_registry, 'read_metadata_cache'):
            return self.tool_registry.read_metadata_cache
        return {}

    # ==================== 子代理机制 ====================

    def run_as_subagent(
        self,
        task: str,
        tool_filter: Optional['BaseToolFilter'] = None,
        return_summary: bool = True,
        max_steps_override: Optional[int] = None
    ) -> Dict[str, Any]:
        """作为子代理运行（上下文隔离模式）

        特性：
        - 上下文隔离：创建独立的历史记录，不污染主 Agent 上下文
        - 工具过滤：可选的工具访问控制
        - 摘要返回：返回结构化摘要而非完整历史
        - 状态恢复：执行后自动恢复原始状态

        Args:
            task: 子任务描述
            tool_filter: 工具过滤器（可选），用于限制可用工具
            return_summary: 是否返回摘要（True）或完整结果（False）
            max_steps_override: 覆盖最大步数（可选）

        Returns:
            {
                "success": bool,           # 是否成功完成
                "summary": str,            # 任务摘要（如果 return_summary=True）
                "result": str,             # 完整结果（如果 return_summary=False）
                "metadata": {              # 执行元数据
                    "steps": int,          # 执行步数
                    "tokens": int,         # 消耗 Token 数（估算）
                    "duration_seconds": float,  # 执行时长
                    "tools_used": List[str],    # 使用的工具列表
                    "error": Optional[str]      # 错误信息（如果失败）
                }
            }
        """
        import time

        # 1. 保存当前状态
        original_history = self.history_manager.get_history().copy()
        original_token_count = self._history_token_count
        original_session_metadata = dict(getattr(self, "_session_metadata", {}))
        original_tools = None
        original_max_steps = None

        # 2. 创建隔离的新历史
        self.history_manager.clear()

        # 3. 应用工具过滤（如果提供）
        if tool_filter and self.tool_registry:
            original_tools = self._apply_tool_filter(tool_filter)

        # 4. 覆盖最大步数（如果提供）
        if max_steps_override is not None and hasattr(self, 'max_steps'):
            original_max_steps = self.max_steps
            self.max_steps = max_steps_override

        # 记录开始时间
        start_time = time.time()
        success = False
        result = ""
        error_msg = None

        try:
            # 5. 执行任务
            result = self.run(task)
            success = True

        except KeyboardInterrupt:
            error_msg = "用户中断"
            raise

        except Exception as e:
            error_msg = str(e)
            result = f"执行失败: {error_msg}"

        finally:
            # 记录执行时长
            duration = time.time() - start_time

            # 收集元数据（生成摘要前快照子代理状态，供摘要使用）
            metadata = self._get_subagent_metadata(duration, error_msg)

            # 8. 恢复原始状态（独立 try/finally，保证任何异常下都恢复）
            try:
                self.history_manager.clear()
                for msg in original_history:
                    self.history_manager.append(msg)
                # 恢复 Token 计数（子代理执行期间被 add_message 累加污染）
                self._history_token_count = original_token_count
                # 恢复会话元数据（子代理可能改写 total_steps/total_tokens）
                if hasattr(self, "_session_metadata"):
                    self._session_metadata = dict(original_session_metadata)
                if original_tools is not None:
                    self._restore_tools(original_tools)
                if original_max_steps is not None:
                    self.max_steps = original_max_steps
            except Exception as e:
                self.logger.warning(f"子代理状态恢复失败: {e}")

            # 7. 生成摘要（使用子代理元数据）
            if return_summary:
                summary = self._generate_subagent_summary(task, result, metadata)

        # 9. 返回结果
        if return_summary:
            return {
                "success": success,
                "summary": summary,
                "metadata": metadata
            }
        else:
            return {
                "success": success,
                "result": result,
                "metadata": metadata
            }

    def _apply_tool_filter(self, tool_filter: 'BaseToolFilter') -> List[str]:
        """应用工具过滤器（ v0.1.2 → v0.3：deprecated）。

        推荐替代：使用 `agentorchestra.capability.tools.registry.temporary_tool_filter()`
        （contextvars + RAII），无需手动恢复。

        本方法保留向后兼容但内部已委托到 contextvars。
        """
        if not self.tool_registry:
            return []

        #
        # 此方法保留作为向后兼容层。
        return self.tool_registry.list_tools()

    def _restore_tools(self, original_tools: List[str]) -> None:
        """恢复原始工具列表（ v0.1.2 → v0.3：deprecated，no-op）。

        历史行为：在 `_apply_tool_filter` 中删除的工具重新加回。
        新行为（contextvars 路径）：无需恢复，块退出自动 cleanup。

        本方法保留作为向后兼容层，仅记录 warning。
        """
        if not self.tool_registry:
            return
        #
        # 不再操作 _temp_disabled_* 私有 dict
        self.logger.debug(
            "_restore_tools 已 deprecated（contextvars 自动 cleanup）；参数 %s 忽略",
            len(original_tools),
        )

    def _get_subagent_metadata(self, duration: float, error: Optional[str]) -> Dict[str, Any]:
        """获取子代理执行元数据

        Args:
            duration: 执行时长（秒）
            error: 错误信息（可选）

        Returns:
            元数据字典
        """
        history = self.history_manager.get_history()

        # 估算步数（用户+助手消息对）
        steps = sum(1 for msg in history if msg.role == "assistant")

        # 估算 Token 数（简化：字符数 / 4）
        total_chars = sum(len(msg.content) for msg in history)
        tokens = total_chars // 4

        # 提取使用的工具
        tools_used = self._extract_tools_from_history(history)

        metadata = {
            "steps": steps,
            "tokens": tokens,
            "duration_seconds": round(duration, 2),
            "tools_used": tools_used
        }

        if error:
            metadata["error"] = error

        return metadata

    def _extract_tools_from_history(self, history: List[Message]) -> List[str]:
        """从历史中提取使用的工具

        Args:
            history: 历史消息列表

        Returns:
            工具名称列表（去重）
        """
        tools = set()

        for msg in history:
            # 检查 tool_calls（FunctionCallAgent）
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    if isinstance(tool_call, dict) and 'function' in tool_call:
                        tools.add(tool_call['function'].get('name', ''))

            # 检查内容中的工具调用（ReActAgent）
            if msg.role == "assistant" and "Action:" in msg.content:
                import re
                matches = re.findall(r'Action:\s*(\w+)\[', msg.content)
                tools.update(matches)

        return sorted(list(tools))

    def _generate_subagent_summary(
        self,
        task: str,
        result: str,
        metadata: Dict[str, Any]
    ) -> str:
        """生成子代理执行摘要

        Args:
            task: 任务描述
            result: 执行结果
            metadata: 执行元数据

        Returns:
            摘要文本
        """
        # 截断结果（避免摘要过长）
        result_preview = truncate_text(result, 500)

        # 构建摘要
        summary_parts = [
            f"任务: {task}",
            f"结果: {result_preview}",
            f"步数: {metadata['steps']}",
            f"耗时: {metadata['duration_seconds']}秒"
        ]

        if metadata.get('tools_used'):
            summary_parts.append(f"工具: {', '.join(metadata['tools_used'])}")

        if metadata.get('error'):
            summary_parts.append(f"错误: {metadata['error']}")

        return "\n".join(summary_parts)


    def _create_light_llm(self) -> SymphonyLLM:
        """创建轻量模型 LLM 实例

        Returns:
            轻量模型 LLM 实例
        """
        # 复用主 LLM 的配置，但使用轻量模型
        light_llm = SymphonyLLM(
            provider=self.config.subagent_light_llm_provider,
            model=self.config.subagent_light_llm_model,
            temperature=self.llm.temperature if hasattr(self.llm, 'temperature') else 0.7,
            max_tokens=self.llm.max_tokens if hasattr(self.llm, 'max_tokens') else None
        )

        return light_llm

    # ==================== M0 / P0 - Durable Checkpoint ====================

    async def _save_checkpoint(self, thread_id: str, state: Dict[str, Any],
                                step: Optional[int] = None,
                                metadata: Optional[Dict[str, Any]] = None) -> str:
        """保存当前状态到 checkpoint + WAL。

        Args:
            thread_id: thread id（Agent.run 入口创建或复用）
            state: 序列化状态（如 {"history": [...], "step": int}）
            step: 步骤序号
            metadata: 任意元数据

        Returns:
            checkpoint_id
        """
        if self.checkpoint_store is None:
            return ""
        import uuid as _uuid

        from agentorchestra.orchestration.state.checkpoint import Checkpoint

        # 确保 thread 存在
        thread = await self.checkpoint_store.get_thread(thread_id)
        if not thread:
            await self.checkpoint_store.create_thread(thread_id)

        # 找 parent_id
        latest = await self.checkpoint_store.latest_checkpoint(thread_id)
        parent_id = latest.checkpoint_id if latest else None

        cp = Checkpoint(
            thread_id=thread_id,
            checkpoint_id=f"cp-{step or 0}-{_uuid.uuid4().hex[:8]}",
            parent_id=parent_id,
            state=state,
            metadata={**(metadata or {}), "step": step} if step else (metadata or {}),
        )
        await self.checkpoint_store.save_checkpoint(cp)

        # 同步写 WAL
        from agentorchestra.orchestration.state.wal import WALActionType, WALEntry
        await self.checkpoint_store.append_wal(WALEntry(
            thread_id=thread_id,
            action_type=WALActionType.CHECKPOINT,
            payload={
                "checkpoint_id": cp.checkpoint_id,
                "parent_id": parent_id,
                "step": step,
            },
        ))

        # flush ontology WAL queue（collect-and-flush）
        if self._wal_flush_target is not None:
            try:
                entries = self._wal_flush_target.drain_wal()
                for e in entries:
                    await self.checkpoint_store.append_wal(WALEntry(
                        thread_id=e["thread_id"],
                        action_type=e["action_type"],
                        payload=e["payload"],
                    ))
            except Exception as ex:
                if self.config.debug:
                    self.logger.debug(f"ontology WAL flush failed: {ex}")

        return cp.checkpoint_id

    async def resume(self, thread_id: str, checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
        """从指定 checkpoint 恢复状态。

        Args:
            thread_id: thread id
            checkpoint_id: 指定 checkpoint（默认最新）

        Returns:
            恢复的 state 字典（不含执行；调用方需自行注入到 Agent.run）
        """
        if self.checkpoint_store is None:
            raise RuntimeError("Checkpoint store 未启用")

        cp = None
        if checkpoint_id:
            cp = await self.checkpoint_store.load_checkpoint(thread_id, checkpoint_id)
        if cp is None:
            cp = await self.checkpoint_store.latest_checkpoint(thread_id)
        if cp is None:
            raise FileNotFoundError(f"thread {thread_id} 没有 checkpoint")

        self._active_thread_id = thread_id
        # 设置 ontology WAL context
        if self.ontology_engine is not None:
            obj_store = getattr(self.ontology_engine, "object_store", None)
            if obj_store is not None:
                obj_store.set_wal_thread_id(thread_id)

        return dict(cp.state)

    def interrupt(self, reason: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """发起 HITL 中断。

        业务侧捕获 InterruptPending 后调 agent.resume_with(token, response)。


        现在统一检测 loop 状态：
        - 有运行 loop：fire-and-forget create_task（无需等待落库，由 scheduler 后续 flush）
        - 无运行 loop：使用 `asyncio.run()` 同步等待
        - 任意情况都 raise InterruptPending（业务必须捕获）
        """
        import asyncio
        import uuid as _uuid

        from agentorchestra.orchestration.state.interrupt import Interrupt, InterruptPending

        if self.checkpoint_store is None:
            raise RuntimeError("Checkpoint store 未启用")

        thread_id = self._active_thread_id or "default"
        token = f"int-{_uuid.uuid4().hex}"
        checkpoint_id = ""  # 调用方应在前一帧 _save_checkpoint

        intr = Interrupt(
            token=token,
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            reason=reason,
            payload=payload or {},
        )

        #使用 try 检测 loop 状态，避免 get_event_loop() 本身在某些 Python 版本中抛
        # DeprecationWarning（get_event_loop() 在 Python 3.10+ 无 loop 时已不建议使用）
        try:
            running_loop = asyncio.get_running_loop()
            # 在已有 loop 中：fire-and-forget 落库（用户捕获 InterruptPending 后会等同步逻辑）
            running_loop.create_task(
                self.checkpoint_store.create_interrupt(intr),
                name=f"interrupt-create-{token}",
            )
        except RuntimeError:
            # 无运行 loop：同步等待落库
            asyncio.run(self.checkpoint_store.create_interrupt(intr))

        raise InterruptPending(token=token, reason=reason, payload=payload or {})

    async def resume_with(self, token: str, response: Dict[str, Any]) -> None:
        """恢复被 interrupt 暂停的 Agent。

        Args:
            token: interrupt 时返回的 token
            response: 业务侧响应（如 {"approved": True}）
        """
        if self.checkpoint_store is None:
            raise RuntimeError("Checkpoint store 未启用")
        await self.checkpoint_store.resolve_interrupt(token, response)
        self._pending_resume_response = response

    # ==================== M4 / P4 - 并发收敛 ====================

    def get_subagent_semaphore(self):
        """获取子 Agent 并发信号量（按 Config.max_concurrent_subagents 懒建）。"""
        if self._subagent_semaphore is None:
            limit = max(1, int(getattr(self.config, "max_concurrent_subagents", 2)))
            self._subagent_semaphore = asyncio.Semaphore(limit)
        return self._subagent_semaphore

    async def run_subagents_concurrently(
        self,
        tasks: List[Callable[[], Any]],
    ) -> List[Any]:
        """并发执行一组子 Agent 任务，受 max_concurrent_subagents 信号量限流。

        Args:
            tasks: 无参 async callable 列表（如 [lambda: agent.arun(t), ...]）

        Returns:
            各任务结果（保序）
        """
        sem = self.get_subagent_semaphore()

        async def _run(task):
            async with sem:
                result = task()
                if asyncio.iscoroutine(result):
                    return await result
                return await asyncio.to_thread(result) if callable(result) else result

        return await asyncio.gather(*[_run(t) for t in tasks])

    def get_concurrency_info(self) -> Dict[str, Any]:
        """当前并发配置摘要（供观测/测试）。"""
        return {
            "max_concurrent_subagents": max(
                1, int(getattr(self.config, "max_concurrent_subagents", 2))),
            "max_concurrent_tools": int(
                getattr(self.config, "max_concurrent_tools", 3)),
        }
