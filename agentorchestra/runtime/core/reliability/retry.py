"""通用重试机制

提供带指数退避的重试装饰器，用于 LLM 调用等不稳定的外部操作。
"""

import functools
import time
from typing import Any, Callable, Optional, Tuple, Type

from agentorchestra.runtime.core.exceptions import SymphonyException


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
):
    """带指数退避的重试装饰器

    Args:
        max_retries: 最大重试次数（0 = 不重试）
        base_delay: 基础退避延迟（秒）
        backoff_factor: 退避指数系数（delay = base * factor^attempt）
        retryable_exceptions: 可重试的异常类型元组；
            None 时只重试 SymphonyException

    Returns:
        装饰器函数

    Example:
        @retry_with_backoff(max_retries=3, base_delay=0.5)
        def call_llm():
            return adapter.invoke(messages)
    """
    exceptions = retryable_exceptions or (SymphonyException,)

    def decorator(func: Callable) -> Callable:
        """将函数包装为带重试逻辑的版本"""
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            """执行目标函数，失败时按指数退避重试"""
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt >= max_retries:
                        break
                    delay = base_delay * (backoff_factor ** attempt)
                    time.sleep(delay)
            raise last_error
        return wrapper
    return decorator


class RetryManager:
    """可配置的重试管理器（供 SymphonyLLM 等对象持有）"""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        backoff_factor: float = 2.0,
    ):
        """初始化重试管理器

        Args:
            max_retries: 最大重试次数
            base_delay: 基础退避延迟（秒）
            backoff_factor: 退避指数系数
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor
        self.retry_count = 0  # 统计实际重试次数

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """执行函数，失败时重试（带指数退避）

        Args:
            func: 要执行的函数
            *args, **kwargs: 传给函数的参数

        Returns:
            函数结果

        Raises:
            SymphonyException: 所有重试均失败时抛出
        """
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                last_error = e
                if attempt >= self.max_retries:
                    break
                self.retry_count += 1
                delay = self.base_delay * (self.backoff_factor ** attempt)
                time.sleep(delay)

        if isinstance(last_error, SymphonyException):
            raise last_error
        raise SymphonyException(f"操作重试 {self.max_retries} 次后失败: {last_error}")

    def reset(self) -> None:
        """重置重试计数"""
        self.retry_count = 0
