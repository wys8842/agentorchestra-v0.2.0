"""配置加载工具

支持从环境变量和 JSON 文件加载配置，提供密钥脱敏。
"""

import os
from typing import Any, Dict, Optional

from agentorchestra.runtime.core.utils import safe_json_load

# 需要脱敏的配置键（API Key、密钥等）
SENSITIVE_KEYS = {
    "api_key", "apikey", "api-key",
    "secret", "token", "password",
    "llm_api_key", "anthropic_api_key", "gemini_api_key",
    "mcp_headers",
}


class ConfigLoader:
    """配置加载器"""

    @staticmethod
    def from_env(env_prefix: str = "") -> Dict[str, Any]:
        """从环境变量收集配置

        约定：配置项的环境变量名为 大写配置名（如 default_model -> DEFAULT_MODEL）
        支持 env_prefix 前缀（如 "SYMPHONY_" -> SYMPHONY_DEFAULT_MODEL）

        Args:
            env_prefix: 环境变量前缀（如 "SYMPHONY_"）

        Returns:
            配置字典
        """
        result = {}
        for key in _known_config_keys():
            env_name = f"{env_prefix}{key.upper()}"
            value = os.getenv(env_name)
            if value is not None:
                result[key] = value
        return result

    @staticmethod
    def from_file(path: str) -> Dict[str, Any]:
        """从 JSON 文件加载配置

        Args:
            path: JSON 配置文件路径

        Returns:
            配置字典
        """
        if not os.path.exists(path):
            return {}
        data = safe_json_load(path, default={})
        if not isinstance(data, dict):
            return {}
        return data

    @staticmethod
    def load(config_cls, file_path: Optional[str] = None,
             env_prefix: str = "", **overrides) -> Any:
        """综合加载配置

        优先级：显式参数 > 配置文件 > 环境变量 > 默认值

        Args:
            config_cls: Config 类
            file_path: 配置文件路径（可选）
            env_prefix: 环境变量前缀
            **overrides: 显式覆盖参数

        Returns:
            Config 实例
        """
        # 从环境变量加载
        data = ConfigLoader.from_env(env_prefix)
        # 从文件加载（覆盖 env）
        if file_path:
            file_data = ConfigLoader.from_file(file_path)
            data.update(file_data)
        # 显式参数（最高优先级）
        data.update(overrides)
        return config_cls(**data)

    @staticmethod
    def sanitize(config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """脱敏配置中的密钥（用于日志/审计展示）

        Args:
            config_dict: 配置字典

        Returns:
            脱敏后的配置字典（敏感值替换为 ***）
        """
        result = {}
        for key, value in config_dict.items():
            if _is_sensitive(key) and value not in (None, ""):
                result[key] = "***"
            else:
                result[key] = value
        return result


def _known_config_keys() -> list:
    """返回已知配置键列表（用于 env 加载）"""
    return [
        "default_model", "default_provider", "temperature", "max_tokens",
        "debug", "log_level",
        "max_history_length",
        "context_window", "compression_threshold", "min_retain_rounds",
        "enable_smart_compression",
        "context_builder_enabled", "context_builder_max_tokens",
        "summary_llm_provider", "summary_llm_model",
        "summary_max_tokens", "summary_temperature",
        "tool_output_max_lines", "tool_output_max_bytes",
        "tool_output_dir", "tool_output_truncate_direction",
        "trace_enabled", "trace_dir", "trace_sanitize",
        "trace_html_include_raw_response",
        "skills_enabled", "skills_dir", "skills_auto_register",
        "mcp_enabled", "mcp_config_file",
        "ontology_engine_enabled", "ontology_engine_module",
        "ontology_default_principal", "ontology_default_roles",
        "ontology_backend", "ontology_db_path",
        "circuit_enabled", "circuit_failure_threshold",
        "circuit_recovery_timeout",
        "session_enabled", "session_dir", "auto_save_enabled",
        "auto_save_interval",
        "subagent_enabled", "subagent_max_steps",
        "subagent_use_light_llm", "subagent_light_llm_provider",
        "subagent_light_llm_model",
        "todowrite_enabled", "devlog_enabled",
        "async_enabled", "max_concurrent_tools", "hook_timeout_seconds",
        "llm_async_timeout", "tool_async_timeout",
        "stream_enabled", "stream_buffer_size",
        "stream_include_thinking", "stream_include_tool_calls",
    ]


def _is_sensitive(key: str) -> bool:
    """判断配置键是否敏感"""
    lower = key.lower()
    return any(s in lower for s in SENSITIVE_KEYS)
