"""配置管理 - 拆分为子配置（Phase 3：API 收敛）

设计原则：
- **opt-in by default**：所有非核心 feature 默认 False，避免隐式开启文件扫描/磁盘持久化/MCP 等副作用
- 子配置类分组：LLMConfig / HistoryConfig / ContextConfig / ObservabilityConfig / ToolConfig / StateConfig /
  SubAgentConfig / MemoryConfig / SkillsConfig / MCPConfig / SessionConfig / OntologyConfig
- 顶层 Config 仅持有子配置 + 跨子系统的少量字段（max_concurrent_* 等）
- 提供 Config.development() / Config.production() 预设
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# 模块级常量（避免 Pydantic BaseModel 清理类级 dict）
_LEGACY_FIELD_MAP: Dict[str, tuple] = {
    "default_model": ("llm", "default_model"),
    "default_provider": ("llm", "default_provider"),
    "temperature": ("llm", "temperature"),
    "max_tokens": ("llm", "max_tokens"),
    "debug": ("system", "debug"),
    "log_level": ("system", "log_level"),
    "max_concurrent_tools": ("system", "max_concurrent_tools"),
    "max_concurrent_subagents": ("system", "max_concurrent_subagents"),
    "hook_timeout_seconds": ("system", "hook_timeout_seconds"),
    "max_history_length": ("history", "max_history_length"),
    "context_window": ("history", "context_window"),
    "compression_threshold": ("history", "compression_threshold"),
    "min_retain_rounds": ("history", "min_retain_rounds"),
    "enable_smart_compression": ("smart_compression", "enabled"),
    "summary_llm_provider": ("smart_compression", "summary_llm_provider"),
    "summary_llm_model": ("smart_compression", "summary_llm_model"),
    "summary_max_tokens": ("smart_compression", "summary_max_tokens"),
    "summary_temperature": ("smart_compression", "summary_temperature"),
    "context_builder_enabled": ("context_builder", "enabled"),
    "context_builder_max_tokens": ("context_builder", "max_tokens"),
    "trace_enabled": ("trace", "enabled"),
    "trace_dir": ("trace", "output_dir"),
    "trace_sanitize": ("trace", "sanitize"),
    "trace_html_include_raw_response": ("trace", "html_include_raw_response"),
    "tool_output_max_lines": ("tool_output", "max_lines"),
    "tool_output_max_bytes": ("tool_output", "max_bytes"),
    "tool_output_dir": ("tool_output", "output_dir"),
    "tool_output_truncate_direction": ("tool_output", "truncate_direction"),
    "circuit_enabled": ("circuit_breaker", "enabled"),
    "circuit_failure_threshold": ("circuit_breaker", "failure_threshold"),
    "circuit_recovery_timeout": ("circuit_breaker", "recovery_timeout"),
    "skills_enabled": ("skills", "enabled"),
    "skills_dir": ("skills", "dir"),
    "skills_auto_register": ("skills", "auto_register"),
    "mcp_enabled": ("mcp", "enabled"),
    "mcp_config_file": ("mcp", "config_file"),
    "ontology_engine_enabled": ("ontology", "engine_enabled"),
    "ontology_engine_module": ("ontology", "engine_module"),
    "ontology_default_principal": ("ontology", "default_principal"),
    "ontology_default_roles": ("ontology", "default_roles"),
    "ontology_backend": ("ontology", "backend"),
    "ontology_db_path": ("ontology", "db_path"),
    "session_enabled": ("session", "enabled"),
    "session_dir": ("session", "dir"),
    "auto_save_enabled": ("session", "auto_save_enabled"),
    "auto_save_interval": ("session", "auto_save_interval"),
    "state_checkpoint_enabled": ("state_checkpoint", "enabled"),
    "persistence_mode": ("state_checkpoint", "persistence_mode"),
    "state_db_url": ("state_checkpoint", "db_url"),
    "wal_snapshot_enabled": ("state_checkpoint", "wal_snapshot_enabled"),
    "wal_snapshot_threshold": ("state_checkpoint", "wal_snapshot_threshold"),
    "wal_snapshot_interval_seconds": ("state_checkpoint", "wal_snapshot_interval_seconds"),
    "subagent_enabled": ("subagent", "enabled"),
    "subagent_max_steps": ("subagent", "max_steps"),
    "subagent_use_light_llm": ("subagent", "use_light_llm"),
    "subagent_light_llm_provider": ("subagent", "light_llm_provider"),
    "subagent_light_llm_model": ("subagent", "light_llm_model"),
    "todowrite_enabled": ("todowrite", "enabled"),
    "todowrite_persistence_dir": ("todowrite", "persistence_dir"),
    "devlog_enabled": ("devlog", "enabled"),
    "devlog_persistence_dir": ("devlog", "persistence_dir"),
    "memory_enabled": ("memory", "enabled"),
    "memory_backend": ("memory", "backend"),
    "memory_db_path": ("memory", "db_path"),
    "memory_jsonl_path": ("memory", "jsonl_path"),
    "memory_namespace": ("memory", "namespace"),
    "memory_auto_register_tools": ("memory", "auto_register_tools"),
    "memory_auto_recall": ("memory", "auto_recall"),
    "memory_auto_summarize": ("memory", "auto_summarize"),
    "memory_recall_top_k": ("memory", "recall_top_k"),
    "memory_embedding_enabled": ("memory", "embedding_enabled"),
    "memory_dedup_threshold": ("memory", "dedup_threshold"),
    "memory_max_entries": ("memory", "max_entries"),
    "memory_decay_enabled": ("memory", "decay_enabled"),
    "memory_decay_tau_min_days": ("memory", "decay_tau_min_days"),
    "memory_decay_tau_max_days": ("memory", "decay_tau_max_days"),
}

_SUB_NAMES = (
    "llm", "system", "history", "smart_compression", "context_builder",
    "trace", "tool_output", "circuit_breaker", "skills", "mcp", "ontology",
    "session", "state_checkpoint", "subagent", "todowrite", "devlog", "memory",
)


# ---------------- 子配置类 ----------------


class LLMConfig(BaseModel):
    """LLM 配置（核心；默认开启）"""
    default_model: str = "gpt-3.5-turbo"
    default_provider: str = "openai"
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    max_retries: int = 3
    retry_base_delay: float = 1.0
    timeout: int = 60


class SystemConfig(BaseModel):
    """系统级配置（核心；默认开启）"""
    debug: bool = False
    log_level: str = "WARNING"  #默认 WARNING（生产友好）
    max_concurrent_tools: int = 3
    max_concurrent_subagents: int = 2
    hook_timeout_seconds: float = 5.0


class HistoryConfig(BaseModel):
    """历史管理配置（核心；默认开启）"""
    max_history_length: int = 100
    context_window: int = 128000
    compression_threshold: float = 0.8
    min_retain_rounds: int = 10


class SmartCompressionConfig(BaseModel):
    """智能摘要配置（opt-in；隐式 LLM 成本）"""
    enabled: bool = False
    summary_llm_provider: str = "deepseek"
    summary_llm_model: str = "deepseek-chat"
    summary_max_tokens: int = 800
    summary_temperature: float = 0.3


class ContextBuilderConfig(BaseModel):
    """GSSC 上下文构建器（opt-in；依赖 tiktoken）"""
    enabled: bool = False
    max_tokens: int = 8000


class TraceConfig(BaseModel):
    """可观测 Trace 配置（opt-in）"""
    enabled: bool = False  #默认 False，避免隐式文件 I/O
    output_dir: str = "memory/traces"
    sanitize: bool = True
    html_include_raw_response: bool = False


class ToolOutputConfig(BaseModel):
    """工具输出截断配置（核心；默认开启）"""
    max_lines: int = 2000
    max_bytes: int = 51200
    output_dir: str = "tool-output"
    truncate_direction: str = "head"


class CircuitBreakerConfig(BaseModel):
    """熔断器配置（核心；默认开启）"""
    enabled: bool = True
    failure_threshold: int = 3
    recovery_timeout: int = 300


class SkillsConfig(BaseModel):
    """Skills 知识外化（opt-in；启动时扫描 skills_dir）"""
    enabled: bool = False  #默认 False，避免隐式文件扫描
    dir: str = "skills"
    auto_register: bool = True


class MCPConfig(BaseModel):
    """Model Context Protocol（opt-in）"""
    enabled: bool = False
    config_file: str = "mcp.json"


class OntologyConfig(BaseModel):
    """企业级 Ontology（opt-in）"""
    engine_enabled: bool = False
    engine_module: str = ""
    default_principal: str = "agent"
    default_roles: List[str] = []
    backend: str = "memory"
    db_path: str = "memory/ontology.db"


class SessionConfig(BaseModel):
    """会话持久化（opt-in）"""
    enabled: bool = False  #默认 False
    dir: str = "memory/sessions"
    auto_save_enabled: bool = False
    auto_save_interval: int = 10


class StateCheckpointConfig(BaseModel):
    """M0 durable checkpoint（opt-in）"""
    enabled: bool = False  #默认 False
    persistence_mode: str = "sqlite"
    db_url: str = ""
    wal_snapshot_enabled: bool = False
    wal_snapshot_threshold: int = 1000
    wal_snapshot_interval_seconds: float = 60.0


class SubAgentConfig(BaseModel):
    """子代理机制（opt-in）"""
    enabled: bool = False  #默认 False
    max_steps: int = 15
    use_light_llm: bool = False
    light_llm_provider: str = "deepseek"
    light_llm_model: str = "deepseek-chat"


class TodoWriteConfig(BaseModel):
    """TodoWrite 进度管理（opt-in）"""
    enabled: bool = False  #默认 False
    persistence_dir: str = "memory/todos"


class DevLogConfig(BaseModel):
    """DevLog 开发日志（opt-in）"""
    enabled: bool = False  #默认 False
    persistence_dir: str = "memory/devlogs"


class MemoryConfig(BaseModel):
    """跨会话持久记忆（opt-in；启动时建库）"""
    enabled: bool = False  #默认 False
    backend: str = "sqlite"
    db_path: str = "memory/memories.db"
    jsonl_path: str = "memory/memories.jsonl"
    namespace: str = "default"
    auto_register_tools: bool = True
    auto_recall: bool = True
    auto_summarize: bool = False
    recall_top_k: int = 5
    embedding_enabled: bool = True
    dedup_threshold: float = 0.92
    max_entries: int = 10000
    decay_enabled: bool = False
    decay_tau_min_days: float = 7.0
    decay_tau_max_days: float = 180.0


# ---------------- 顶层 Config（facade） ----------------


class Config(BaseModel):
    """Symphony 顶层配置。

    由若干子配置聚合；提供 Config.development() / Config.production() 预设。
    所有 feature flag 默认 False（opt-in by default）。
    """

    llm: LLMConfig = LLMConfig()
    system: SystemConfig = SystemConfig()
    history: HistoryConfig = HistoryConfig()
    smart_compression: SmartCompressionConfig = SmartCompressionConfig()
    context_builder: ContextBuilderConfig = ContextBuilderConfig()
    trace: TraceConfig = TraceConfig()
    tool_output: ToolOutputConfig = ToolOutputConfig()
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()
    skills: SkillsConfig = SkillsConfig()
    mcp: MCPConfig = MCPConfig()
    ontology: OntologyConfig = OntologyConfig()
    session: SessionConfig = SessionConfig()
    state_checkpoint: StateCheckpointConfig = StateCheckpointConfig()
    subagent: SubAgentConfig = SubAgentConfig()
    todowrite: TodoWriteConfig = TodoWriteConfig()
    devlog: DevLogConfig = DevLogConfig()
    memory: MemoryConfig = MemoryConfig()

    # 兼容旧字段：把所有 `Config.xxx` 访问代理到对应子配置
    # 注：Pydantic v2 BaseModel 会清理类级可变默认值（如 dict），
    # 因此 legacy map 必须在模块级定义（_LEGACY_FIELD_MAP）。

    def __getattr__(self, name: str) -> Any:
        # 1) 旧扁平字段名映射
        if name in _LEGACY_FIELD_MAP:
            sub_name, sub_field = _LEGACY_FIELD_MAP[name]
            sub = self.__dict__.get(sub_name) or getattr(self, sub_name, None)
            if sub is not None and hasattr(sub, sub_field):
                return getattr(sub, sub_field)
        # 2) 子配置平铺访问
        for sub_name in _SUB_NAMES:
            sub = self.__dict__.get(sub_name)
            if sub is not None and hasattr(sub, name):
                return getattr(sub, name)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        sub_names_set = set(_SUB_NAMES)
        # 1) 旧扁平字段名映射
        if name in _LEGACY_FIELD_MAP:
            sub_name, sub_field = _LEGACY_FIELD_MAP[name]
            sub = self.__dict__.get(sub_name) or getattr(self, sub_name, None)
            if sub is not None and hasattr(sub, sub_field):
                setattr(sub, sub_field, value)
                return
        # 2) 子配置字段名走 pydantic 默认行为
        if name in sub_names_set or name.startswith("_"):
            super().__setattr__(name, value)
            return
        super().__setattr__(name, value)

    @classmethod
    def development(cls) -> "Config":
        """开发预设：开启 trace/skills/session，方便本地调试。"""
        c = cls()
        c.trace.enabled = True
        c.skills.enabled = True
        c.session.enabled = True
        c.system.log_level = "DEBUG"
        c.system.debug = True
        return c

    @classmethod
    def production(cls) -> "Config":
        """生产预设：全部 opt-in；仅开启核心 feature。"""
        c = cls()
        return c

    @classmethod
    def from_env(cls, env_prefix: str = "") -> "Config":
        """从环境变量创建配置（兼容旧 API；环境变量按 SYMPHONY_<SUB>_<FIELD> 解析）。"""
        from .config_loader import ConfigLoader
        return cls(**ConfigLoader.from_env(env_prefix))

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """从 JSON 配置文件创建配置。"""
        from .config_loader import ConfigLoader
        return cls(**ConfigLoader.from_file(path))

    def sanitized_dict(self) -> Dict[str, Any]:
        """返回脱敏后的配置字典（密钥替换为 ***）。"""
        from .config_loader import ConfigLoader
        return ConfigLoader.sanitize(self.model_dump())

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()