"""结构化日志

提供统一的日志配置与 JSON 结构化输出，支持：
- 控制台（人类可读）或 JSON（机器可解析）格式
- 全局单例 logger 工厂
- 上下文字段（session_id/agent_name/step 等）
"""

import json
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional


class JsonFormatter(logging.Formatter):
    """JSON 结构化日志格式化器"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录为 JSON"""
        log_entry: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 附加 extra 字段
        for key in ("session_id", "agent_name", "step", "event",
                    "tool_name", "model", "duration_ms", "error_code"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        # 异常信息
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """配置全局日志

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）
        json_format: 是否输出 JSON 格式
        log_file: 日志文件路径（可选，启用滚动文件）
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的滚动文件数
    """
    root = logging.getLogger("agentorchestra")
    root.setLevel(level.upper())

    # 避免重复添加 handler
    if root.handlers:
        for h in root.handlers:
            root.removeHandler(h)

    formatter = JsonFormatter() if json_format else logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # 控制台 handler
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件 handler（可选）
    if log_file:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """获取 agentorchestra 命名空间下的 logger

    Args:
        name: 模块名（如 "core.llm"）

    Returns:
        Logger 实例
    """
    return logging.getLogger(f"agentorchestra.{name}")


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    """记录带上下文字段的结构化事件

    Args:
        logger: Logger 实例
        event: 事件名
        **fields: 附加字段（session_id/agent_name 等，会进 JSON）
    """
    logger.info(event, extra=fields)
