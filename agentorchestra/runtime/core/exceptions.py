"""统一异常体系

所有 Symphony 异常继承自 SymphonyException，按模块分层：
- core: LLM / Config / Agent / Session / Stream
- tools: Tool
- context: Context
- observability: Observability
- ontology: Ontology

提供 error_code 属性，便于统一错误分类与映射。
"""

from typing import Optional


class SymphonyException(Exception):
    """Symphony 基础异常类"""

    def __init__(self, message: str = "", error_code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self._default_code()

    @staticmethod
    def _default_code() -> str:
        return "SYMPHONY_ERROR"

    def to_dict(self) -> dict:
        """序列化为字典（便于日志/API 返回）"""
        return {"error_code": self.error_code, "message": self.message}


# ==================== core ====================

class LLMException(SymphonyException):
    """LLM 调用异常"""

    @staticmethod
    def _default_code() -> str:
        return "LLM_ERROR"


class LLMTimeoutException(LLMException):
    """LLM 调用超时"""

    @staticmethod
    def _default_code() -> str:
        return "LLM_TIMEOUT"


class LLMRateLimitException(LLMException):
    """LLM 速率限制"""

    @staticmethod
    def _default_code() -> str:
        return "LLM_RATE_LIMIT"


class ConfigException(SymphonyException):
    """配置异常"""

    @staticmethod
    def _default_code() -> str:
        return "CONFIG_ERROR"


class AgentException(SymphonyException):
    """Agent 执行异常"""

    @staticmethod
    def _default_code() -> str:
        return "AGENT_ERROR"


class SessionException(SymphonyException):
    """会话持久化异常"""

    @staticmethod
    def _default_code() -> str:
        return "SESSION_ERROR"


class StreamException(SymphonyException):
    """流式输出异常"""

    @staticmethod
    def _default_code() -> str:
        return "STREAM_ERROR"


# ==================== tools ====================

class ToolException(SymphonyException):
    """工具异常"""

    @staticmethod
    def _default_code() -> str:
        return "TOOL_ERROR"


class ToolNotFoundException(ToolException):
    """工具不存在"""

    @staticmethod
    def _default_code() -> str:
        return "TOOL_NOT_FOUND"


class ToolExecutionException(ToolException):
    """工具执行异常"""

    @staticmethod
    def _default_code() -> str:
        return "TOOL_EXECUTION_ERROR"


# ==================== context ====================

class ContextException(SymphonyException):
    """上下文工程异常"""

    @staticmethod
    def _default_code() -> str:
        return "CONTEXT_ERROR"


class TokenLimitExceededException(ContextException):
    """Token 预算超限"""

    @staticmethod
    def _default_code() -> str:
        return "TOKEN_LIMIT_EXCEEDED"


# ==================== observability ====================

class ObservabilityException(SymphonyException):
    """可观测性异常"""

    @staticmethod
    def _default_code() -> str:
        return "OBSERVABILITY_ERROR"


# ==================== ontology ====================

class OntologyException(SymphonyException):
    """Ontology 异常"""

    @staticmethod
    def _default_code() -> str:
        return "ONTOLOGY_ERROR"


class ObjectValidationException(OntologyException):
    """对象校验失败"""

    @staticmethod
    def _default_code() -> str:
        return "OBJECT_VALIDATION_ERROR"


class ObjectNotFoundException(OntologyException):
    """对象不存在"""

    @staticmethod
    def _default_code() -> str:
        return "OBJECT_NOT_FOUND"


class PermissionDeniedException(OntologyException):
    """权限拒绝"""

    @staticmethod
    def _default_code() -> str:
        return "PERMISSION_DENIED"


class ActionExecutionException(OntologyException):
    """动作执行失败"""

    @staticmethod
    def _default_code() -> str:
        return "ACTION_EXECUTION_ERROR"
