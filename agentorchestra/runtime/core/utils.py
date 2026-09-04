"""通用工具函数

框架内被多处复用的公共工具，避免重复实现：
- generate_session_id: 统一的会话 ID 生成
- atomic_write: 原子写入文件（临时文件 + 替换）
- serialize_tool_calls: LLM tool_calls 序列化为 OpenAI 消息格式
- measure_elapsed_ms: 计时返回毫秒
- duration_seconds: 计算时间差秒数
- safe_json_load: 安全 JSON 读取（统一异常处理）
- parse_tool_arguments: 解析 LLM tool_call.arguments
- truncate_text: 截断文本（可选添加省略号）
"""

import json
import os
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Union


def generate_session_id(suffix_len: int = 4) -> str:
    """生成唯一的会话 ID

    格式：s-{YYYYMMDD-HHMMSS}-{hex}

    Args:
        suffix_len: 随机后缀长度（默认 4）

    Returns:
        会话 ID
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    unique_suffix = uuid.uuid4().hex[:suffix_len]
    return f"s-{timestamp}-{unique_suffix}"


def atomic_write(filepath: str, data: Any, pretty: bool = False) -> str:
    """原子写入文件（临时文件 + 替换）

    避免写入中途崩溃导致文件损坏。

    Args:
        filepath: 目标文件路径
        data: 要写入的内容（str 直接写；其他对象 JSON 序列化）
        pretty: JSON 是否缩进美化

    Returns:
        写入后的文件路径
    """
    directory = os.path.dirname(filepath)
    if directory:
        os.makedirs(directory, exist_ok=True)

    # 构造内容
    if isinstance(data, str):
        content = data
    else:
        content = json.dumps(data, ensure_ascii=False, indent=2 if pretty else None)

    # 临时文件写入 + 原子替换
    fd, temp_path = tempfile.mkstemp(dir=directory or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temp_path, filepath)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise

    return filepath


def serialize_tool_calls(tool_calls: List[Any]) -> List[Dict[str, Any]]:
    """序列化 LLM 返回的 tool_calls 为 OpenAI 消息格式

    Args:
        tool_calls: LLM 返回的 tool_calls 列表

    Returns:
        序列化后的 tool_calls 列表
    """
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments
            }
        }
        for tc in tool_calls
    ]


def measure_elapsed_ms(start_time: float) -> int:
    """计算从 start_time 到现在经过的毫秒数

    Args:
        start_time: time.time() 返回的开始时间戳

    Returns:
        经过的毫秒数
    """
    return int((time.time() - start_time) * 1000)


def duration_seconds(start: Union[datetime, float], end: Union[datetime, float, None] = None) -> float:
    """计算时间差（秒）

    Args:
        start: 开始时间（datetime 或 time.time() 时间戳）
        end: 结束时间（None 表示当前时间）

    Returns:
        经过的秒数
    """
    if end is None:
        end = datetime.now() if isinstance(start, datetime) else time.time()

    if isinstance(start, datetime) and isinstance(end, datetime):
        return (end - start).total_seconds()

    return float(end) - float(start)  # type: ignore[arg-type]


def safe_json_load(filepath: Union[str, Path], default: Any = None) -> Any:
    """安全读取 JSON 文件

    统一处理 FileNotFoundError 和 JSONDecodeError，调用方无需关心异常。

    Args:
        filepath: JSON 文件路径
        default: 读取失败时返回的默认值

    Returns:
        解析后的数据，失败返回 default
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def parse_tool_arguments(tool_call: Any) -> Dict[str, Any]:
    """解析 LLM 返回的 tool_call.function.arguments

    调用方负责处理 JSONDecodeError（用于报告参数格式错误）。
    此函数仅统一访问入口，避免重复访问 tool_call.function.arguments。

    Args:
        tool_call: LLM 返回的 tool_call 对象（需有 .function.arguments）

    Returns:
        解析后的参数字典

    Raises:
        json.JSONDecodeError: 参数 JSON 格式错误
        AttributeError: tool_call 结构不合法
    """
    return json.loads(tool_call.function.arguments)


def truncate_text(text: str, max_len: int, ellipsis: bool = True) -> str:
    """截断文本到指定长度，超出部分用省略号标记

    Args:
        text: 原始文本
        max_len: 最大长度（含省略号）
        ellipsis: 是否在截断后追加 "..."（默认 True）

    Returns:
        截断后的文本
    """
    if len(text) <= max_len:
        return text
    if ellipsis:
        return text[:max_len] + "..."
    return text[:max_len]
