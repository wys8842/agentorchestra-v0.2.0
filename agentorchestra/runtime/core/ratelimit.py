"""限流与配额

提供：
- TokenBucket: 令牌桶限流（突发 + 稳定速率）
- SlidingWindow: 滑动窗口限流（固定窗口内次数限制）
- RateLimiter: 按 key 的多策略限流器
"""

import threading
import time
from typing import Dict, Optional


class TokenBucket:
    """令牌桶限流

    以固定速率向桶中添加令牌，桶容量限制突发。
    请求需消耗令牌，桶空则拒绝。
    """

    def __init__(self, rate: float, capacity: float):
        """初始化令牌桶

        Args:
            rate: 令牌添加速率（个/秒）
            capacity: 桶容量（最大突发令牌数）
        """
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """尝试获取令牌

        Args:
            tokens: 需要消耗的令牌数

        Returns:
            是否成功获取
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def wait(self, tokens: float = 1.0, timeout: Optional[float] = None) -> bool:
        """阻塞等待令牌

        Args:
            tokens: 需要消耗的令牌数
            timeout: 超时秒数（None 无限等待）

        Returns:
            是否在超时前获得令牌
        """
        start = time.monotonic()
        while not self.try_acquire(tokens):
            if timeout is not None and time.monotonic() - start > timeout:
                return False
            time.sleep(min(0.1, 1.0 / self.rate if self.rate > 0 else 0.1))
        return True


class SlidingWindow:
    """滑动窗口限流（固定窗口内限制次数）"""

    def __init__(self, max_requests: int, window_seconds: float):
        """初始化滑动窗口

        Args:
            max_requests: 窗口内最大请求数
            window_seconds: 窗口时长（秒）
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: list = []
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        """尝试获取许可"""
        with self._lock:
            now = time.monotonic()
            # 清理过期请求
            self._requests = [t for t in self._requests
                              if now - t < self.window_seconds]
            if len(self._requests) >= self.max_requests:
                return False
            self._requests.append(now)
            return True


class RateLimiter:
    """多 key 限流器

    支持按 key（如用户/API key）独立限流。
    """

    def __init__(self, default_limit: int = 100, window_seconds: float = 60.0):
        """初始化限流器

        Args:
            default_limit: 默认窗口内最大请求数
            window_seconds: 窗口时长（秒）
        """
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self._windows: Dict[str, SlidingWindow] = {}
        self._lock = threading.Lock()

    def try_acquire(self, key: str) -> bool:
        """尝试获取许可（按 key）

        Args:
            key: 限流 key（用户/API key 等）

        Returns:
            是否允许
        """
        with self._lock:
            window = self._windows.get(key)
            if window is None:
                window = SlidingWindow(self.default_limit, self.window_seconds)
                self._windows[key] = window
        return window.try_acquire()

    def set_limit(self, key: str, limit: int) -> None:
        """为指定 key 设置独立限额"""
        with self._lock:
            self._windows[key] = SlidingWindow(limit, self.window_seconds)

    def reset(self, key: Optional[str] = None) -> None:
        """重置限流状态"""
        with self._lock:
            if key:
                self._windows.pop(key, None)
            else:
                self._windows.clear()
