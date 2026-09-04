"""配置管理"""

from typing import Any, Dict, Optional

from pydantic import BaseModel


class Config(BaseModel):
    """Symphony配置类"""

    # LLM配置
    default_model: str = "gpt-3.5-turbo"
    default_provider: str = "openai"
    temperature: float = 0.7
    max_tokens: Optional[int] = None

    # 系统配置
    debug: bool = False
    log_level: str = "INFO"

    # 历史管理配置（向后兼容）
    max_history_length: int = 100

    # 上下文工程配置
    context_window: int = 128000  # 上下文窗口大小（tokens）
    compression_threshold: float = 0.8  # 压缩阈值（0.8 = 80%时触发压缩）
    min_retain_rounds: int = 10  # 压缩时保留的最小完整轮次数
    enable_smart_compression: bool = False  # 是否启用智能摘要（需要额外LLM调用）
    context_builder_enabled: bool = False  # 是否启用 GSSC 上下文构建器（融合多路信息）
    context_builder_max_tokens: int = 8000  # GSSC 上下文总预算（tokens）

    # 智能摘要配置
    summary_llm_provider: str = "deepseek"  # 摘要专用 LLM 提供商
    summary_llm_model: str = "deepseek-chat"  # 摘要专用 LLM 模型
    summary_max_tokens: int = 800  # 摘要最大 Token 数
    summary_temperature: float = 0.3  # 摘要生成温度（更确定性）

    # 工具输出截断配置
    tool_output_max_lines: int = 2000  # 工具输出最大行数
    tool_output_max_bytes: int = 51200  # 工具输出最大字节数（50KB）
    tool_output_dir: str = "tool-output"  # 完整输出保存目录
    tool_output_truncate_direction: str = "head"  # 截断方向：head/tail/head_tail

    # 可观测性配置
    trace_enabled: bool = True  # 是否启用 Trace 记录
    trace_dir: str = "memory/traces"  # Trace 文件保存目录
    trace_sanitize: bool = True  # 是否脱敏敏感信息
    trace_html_include_raw_response: bool = False  # HTML 是否包含原始响应

    # Skills 知识外化配置
    skills_enabled: bool = True  # 是否启用 Skills 系统
    skills_dir: str = "skills"  # Skills 目录路径
    skills_auto_register: bool = True  # 是否自动注册 SkillTool

    # MCP (Model Context Protocol) 配置
    mcp_enabled: bool = False  # 是否启用 MCP 工具
    mcp_config_file: str = "mcp.json"  # MCP Server 配置文件路径

    # 企业级 Ontology 配置
    ontology_engine_enabled: bool = False  # 是否启用企业级 Ontology 引擎
    ontology_engine_module: str = ""  # 可选：用户定义的 Ontology 装配模块（含 build_engine()）
    ontology_default_principal: str = "agent"  # 默认安全主体
    ontology_default_roles: list = []  # 默认角色列表
    ontology_backend: str = "memory"  # 对象存储后端: memory/sqlite
    ontology_db_path: str = "memory/ontology.db"  # SQLite 后端数据库文件路径

    # 熔断器配置
    circuit_enabled: bool = True  # 是否启用熔断器
    circuit_failure_threshold: int = 3  # 连续失败多少次后熔断
    circuit_recovery_timeout: int = 300  # 熔断后恢复时间（秒）

    # 会话持久化配置
    session_enabled: bool = True  # 是否启用会话持久化
    session_dir: str = "memory/sessions"  # 会话文件保存目录
    auto_save_enabled: bool = False  # 是否启用自动保存
    auto_save_interval: int = 10  # 自动保存间隔（每N条消息）

    # M0 (P0) - durable checkpoint 持久化（roadmap §2）
    persistence_mode: str = "sqlite"  # sqlite / postgres / in_memory
    state_db_url: str = ""  # SQLAlchemy URL；空则按 persistence_mode 自动选
    state_checkpoint_enabled: bool = True  # Agent.run() 每步保存 checkpoint
    wal_snapshot_threshold: int = 1000  # 快照触发：WAL 条目数
    wal_snapshot_interval_seconds: float = 60.0  # 快照触发：时间间隔
    wal_snapshot_enabled: bool = False  # 后台快照 worker 默认关闭（避免无 loop 时报错）

    # 子代理机制配置
    subagent_enabled: bool = True  # 是否启用子代理机制
    subagent_max_steps: int = 15  # 子代理默认最大步数
    subagent_use_light_llm: bool = False  # 是否使用轻量模型（默认关闭，避免破坏现有行为）
    subagent_light_llm_provider: str = "deepseek"  # 轻量模型提供商
    subagent_light_llm_model: str = "deepseek-chat"  # 轻量模型名称

    # TodoWrite 进度管理配置
    todowrite_enabled: bool = True  # 是否启用 TodoWrite 工具
    todowrite_persistence_dir: str = "memory/todos"  # 任务列表持久化目录

    # DevLog 开发日志配置
    devlog_enabled: bool = True  # 是否启用 DevLog 工具
    devlog_persistence_dir: str = "memory/devlogs"  # 开发日志持久化目录

    # 异步生命周期配置
    async_enabled: bool = True  # 是否启用异步执行
    max_concurrent_tools: int = 3  # 最大并发工具数
    max_concurrent_subagents: int = 2  # M4: 最大并发子 Agent 数（信号量限流）
    hook_timeout_seconds: float = 5.0  # 生命周期钩子超时时间（秒）
    llm_async_timeout: int = 120  # LLM 异步调用超时时间（秒）
    tool_async_timeout: int = 30  # 工具异步调用超时时间（秒）

    # 流式输出配置
    stream_enabled: bool = True  # 是否启用流式输出
    stream_buffer_size: int = 100  # 流式缓冲区大小
    stream_include_thinking: bool = True  # 是否包含思考过程
    stream_include_tool_calls: bool = True  # 是否包含工具调用

    # 记忆系统配置（v1：跨会话持久记忆）
    memory_enabled: bool = False  # 主开关
    memory_backend: str = "sqlite"  # sqlite / jsonl / memory
    memory_db_path: str = "memory/memories.db"  # SQLite 文件
    memory_jsonl_path: str = "memory/memories.jsonl"  # JSONL 文件

    memory_auto_register_tools: bool = True  # 启动时自动注册 MemorySave/Recall 工具
    memory_auto_recall: bool = True  # run 开始自动注入相关记忆
    memory_auto_summarize: bool = False  # run 结束自动提炼（默认关闭，隐式 LLM 成本）

    memory_recall_top_k: int = 5
    memory_embedding_enabled: bool = True  # 关闭则纯关键词
    memory_dedup_threshold: float = 0.92  # 写入去重相似度阈值
    memory_max_entries: int = 10000  # 容量上限（v1 仅记录统计）

    # 多命名空间（v1.1）
    memory_namespace: str = "default"  # Agent 默认 namespace

    # 衰减机制（v1.1，默认关闭）
    memory_decay_enabled: bool = False
    memory_decay_tau_min_days: float = 7.0  # importance=0 的半衰期
    memory_decay_tau_max_days: float = 180.0  # importance=1 的半衰期

    @classmethod
    def from_env(cls, env_prefix: str = "") -> "Config":
        """从环境变量创建配置

        读取所有已知配置项的环境变量（大写名，可加前缀）。

        Args:
            env_prefix: 环境变量前缀（如 "SYMPHONY_"）

        Example:
            >>> config = Config.from_env("SYMPHONY_")
            >>> # 读取 SYMPHONY_DEFAULT_MODEL, SYMPHONY_DEBUG 等
        """
        from .config_loader import ConfigLoader
        return cls(**ConfigLoader.from_env(env_prefix))

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """从 JSON 配置文件创建配置

        Args:
            path: JSON 配置文件路径

        Example:
            >>> config = Config.from_file("config.json")
        """
        from .config_loader import ConfigLoader
        return cls(**ConfigLoader.from_file(path))

    def sanitized_dict(self) -> Dict[str, Any]:
        """返回脱敏后的配置字典（密钥替换为 ***）"""
        from .config_loader import ConfigLoader
        return ConfigLoader.sanitize(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump()
