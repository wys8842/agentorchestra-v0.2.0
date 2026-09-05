"""Prompt 注入防护

提供：
- 输入清洗：检测和标记用户输入中的可疑内容
- 工具输出标记：避免 LLM 误用工具输出
- 危险操作检测：敏感操作需要二次确认
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class ThreatLevel(Enum):
    """威胁等级"""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SanitizeResult:
    """清洗结果"""
    original: str
    sanitized: str
    threat_level: ThreatLevel
    threats: List[str] = field(default_factory=list)
    confidence: float = 1.0


class PromptSanitizer:
    """Prompt 注入检测与清洗"""

    # 常见注入模式
    INJECTION_PATTERNS = [
        # 角色覆盖尝试
        (r"(?i)(ignore|forget|disregard)\s+(all|previous|above)\s+(instructions?|prompts?)", ThreatLevel.HIGH),
        (r"(?i)you\s+are\s+(now|a|an)\s+(?!assistant|helpful)", ThreatLevel.HIGH),
        (r"(?i)system\s*(prompt|message|role)\s*:", ThreatLevel.HIGH),
        # 越权操作尝试
        (r"(?i)(execute|run|eval)\s+(code|script|command|shell)", ThreatLevel.HIGH),
        (r"(?i)(rm\s+-rf|delete\s+all|format\s+c:|drop\s+table)", ThreatLevel.CRITICAL),
        # 数据窃取
        (r"(?i)(show|reveal|print|leak).*?(api[_-]?key|password|secret|token|credential)", ThreatLevel.CRITICAL),
        (r"(?i)(dump|export|exfiltrate).*?(database|user|payment|credit)", ThreatLevel.HIGH),
        # 隐藏指令
        (r"<\|im_start\|>", ThreatLevel.CRITICAL),
        (r"<\|im_end\|>", ThreatLevel.CRITICAL),
        (r"\[INST\]|\[/INST\]", ThreatLevel.CRITICAL),
        (r"<\|system\|>|<\|user\|>|<\|assistant\|>", ThreatLevel.HIGH),
        # 越狱提示
        (r"(?i)dan\s+mode", ThreatLevel.MEDIUM),
        (r"(?i)developer\s+mode", ThreatLevel.MEDIUM),
        (r"(?i)jailbreak", ThreatLevel.MEDIUM),
    ]

    # 编码绕过检测
    ENCODING_PATTERNS = [
        (r"\\x[0-9a-f]{2}", "hex_escape"),
        (r"\\u[0-9a-f]{4}", "unicode_escape"),
        (r"base64[,:]\s*[A-Za-z0-9+/=]{20,}", "base64_payload"),
    ]

    # 工具输出敏感字段
    SENSITIVE_PATTERNS = [
        (r"sk-[a-zA-Z0-9]{20,}", "openai_api_key"),
        (r"sk-ant-[a-zA-Z0-9-]{20,}", "anthropic_api_key"),
        (r"AKIA[0-9A-Z]{16}", "aws_access_key"),
        (r"(?i)password[=:]\s*\S+", "password"),
        (r"(?i)token[=:]\s*[A-Za-z0-9._-]{20,}", "token"),
    ]

    def __init__(
        self,
        enable_sanitize: bool = True,
        enable_sensitive_mask: bool = True,
        block_threshold: ThreatLevel = ThreatLevel.HIGH,
    ):
        self.enable_sanitize = enable_sanitize
        self.enable_sensitive_mask = enable_sensitive_mask
        self.block_threshold = block_threshold

    def sanitize_input(self, text: str) -> SanitizeResult:
        """清洗用户输入

        Args:
            text: 用户输入文本

        Returns:
            SanitizeResult: 包含清洗结果和威胁评估
        """
        threats = []
        max_threat = ThreatLevel.NONE
        sanitized = text

        # 1. 检测注入模式
        for pattern, threat in self.INJECTION_PATTERNS:
            if re.search(pattern, text):
                threat_name = f"injection:{pattern[:30]}"
                threats.append(threat_name)
                if self._threat_level_value(threat) > self._threat_level_value(max_threat):
                    max_threat = threat

        # 2. 检测编码绕过
        for pattern, name in self.ENCODING_PATTERNS:
            if re.search(pattern, text):
                threats.append(f"encoding:{name}")
                if self._threat_level_value(ThreatLevel.HIGH) > self._threat_level_value(max_threat):
                    max_threat = ThreatLevel.HIGH

        # 3. 检测敏感字段（如果是 LLM 输入，记录但不阻断）
        for pattern, name in self.SENSITIVE_PATTERNS:
            if re.search(pattern, text):
                threats.append(f"sensitive:{name}")
                if self._threat_level_value(ThreatLevel.MEDIUM) > self._threat_level_value(max_threat):
                    max_threat = ThreatLevel.MEDIUM

        # 4. 检测超长输入（潜在 DoS）
        if len(text) > 50000:
            threats.append("oversized_input")
            if self._threat_level_value(ThreatLevel.MEDIUM) > self._threat_level_value(max_threat):
                max_threat = ThreatLevel.MEDIUM

        confidence = self._calculate_confidence(threats)

        return SanitizeResult(
            original=text,
            sanitized=sanitized,
            threat_level=max_threat,
            threats=threats,
            confidence=confidence,
        )

    def mask_tool_output(self, output: str) -> str:
        """遮蔽工具输出中的敏感信息

        Args:
            output: 工具原始输出

        Returns:
            遮蔽后的输出
        """
        if not self.enable_sensitive_mask:
            return output

        masked = output
        for pattern, name in self.SENSITIVE_PATTERNS:
            masked = re.sub(pattern, f"[MASKED:{name}]", masked)
        return masked

    def is_safe(self, result: SanitizeResult) -> bool:
        """判断输入是否安全（低于阻断阈值）"""
        return self._threat_level_value(
            result.threat_level
        ) < self._threat_level_value(self.block_threshold)

    @staticmethod
    def _threat_level_value(level: ThreatLevel) -> int:
        """威胁等级数值化（用于比较）"""
        return {
            ThreatLevel.NONE: 0,
            ThreatLevel.LOW: 1,
            ThreatLevel.MEDIUM: 2,
            ThreatLevel.HIGH: 3,
            ThreatLevel.CRITICAL: 4,
        }.get(level, 0)

    @staticmethod
    def _calculate_confidence(threats: List[str]) -> float:
        """计算威胁检测置信度"""
        if not threats:
            return 1.0
        return min(0.5 + len(threats) * 0.15, 0.99)


@dataclass
class DangerousOperation:
    """危险操作定义"""
    name: str
    pattern: str  # 检测模式（正则）
    description: str
    require_confirmation: bool = True
    block_threshold: ThreatLevel = ThreatLevel.HIGH


class OperationGuard:
    """危险操作守护"""

    DEFAULT_OPERATIONS = [
        DangerousOperation(
            name="file_delete",
            pattern=r"(?i)(rm\s+|delete|unlink)\s+\S+",
            description="文件删除操作",
        ),
        DangerousOperation(
            name="system_command",
            pattern=r"(?i)(sudo|systemctl|service|kill|shutdown)",
            description="系统级命令",
        ),
        DangerousOperation(
            name="data_export",
            pattern=r"(?i)(dump|export|backup)\s+.{0,30}(database|all\s+tables)",
            description="数据导出/备份",
        ),
        DangerousOperation(
            name="credential_access",
            pattern=r"(?i)(read|cat|show).*?\.(env|key|pem|p12)",
            description="凭证访问",
        ),
    ]

    def __init__(self, custom_operations: Optional[List[DangerousOperation]] = None):
        self.operations = custom_operations or self.DEFAULT_OPERATIONS
        self._confirmed_set = set()  # 已确认的操作 ID

    def check_operation(self, content: str) -> List[DangerousOperation]:
        """检测危险操作

        Args:
            content: 待检测内容

        Returns:
            匹配到的危险操作列表
        """
        matched = []
        for op in self.operations:
            if re.search(op.pattern, content):
                matched.append(op)
        return matched

    def requires_confirmation(self, op: DangerousOperation) -> bool:
        """操作是否需要确认"""
        return op.require_confirmation

    def confirm(self, op_name: str, content_hash: str) -> str:
        """确认危险操作

        Args:
            op_name: 操作名称
            content_hash: 内容 hash（确保确认的是当前操作）

        Returns:
            确认 ID
        """
        confirm_id = f"{op_name}:{content_hash}"
        self._confirmed_set.add(confirm_id)
        return confirm_id

    def is_confirmed(self, op_name: str, content_hash: str) -> bool:
        """检查操作是否已确认"""
        return f"{op_name}:{content_hash}" in self._confirmed_set


def wrap_tool_output(tool_name: str, output: str, sanitizer: PromptSanitizer) -> str:
    """包装工具输出（标记边界 + 遮蔽敏感信息）

    用于在 messages 中插入 tool 消息时，让 LLM 清楚区分工具输出和用户输入。

    Args:
        tool_name: 工具名称
        output: 原始输出
        sanitizer: 清洗器实例

    Returns:
        包装后的输出
    """
    masked = sanitizer.mask_tool_output(output)
    return (
        f"<<tool_output name={tool_name}>>\n"
        f"{masked}\n"
        f"<</tool_output>>"
    )


def sanitize_user_input(
    text: str, sanitizer: Optional[PromptSanitizer] = None
) -> Tuple[str, SanitizeResult]:
    """用户输入清洗快捷函数

    Args:
        text: 用户输入
        sanitizer: 清洗器（默认创建一个）

    Returns:
        (sanitized_text, result)
    """
    if sanitizer is None:
        sanitizer = PromptSanitizer()
    result = sanitizer.sanitize_input(text)
    return result.sanitized, result