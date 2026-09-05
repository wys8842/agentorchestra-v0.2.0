"""配置热更新

完整接入：
- ConfigWatch: 监听配置文件变更，自动重新加载 Config
- Components.on_config_change(): 全局回调注册
- Agent / LLM / RateLimiter 自动响应配置变更
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, List, Optional

from .config_loader import ConfigLoader

_logger = logging.getLogger("agentorchestra.core.hot_config")

# 全局回调注册表（按名称分组）
_config_callbacks: List[Callable[[Any, Any], None]] = []
_config_callbacks_lock = threading.RLock()
_global_config_watcher: Optional["ConfigWatch"] = None


def register_config_callback(callback: Callable[[Any, Any], None]) -> None:
    """注册全局配置变更回调"""
    with _config_callbacks_lock:
        if callback not in _config_callbacks:
            _config_callbacks.append(callback)


def unregister_config_callback(callback: Callable[[Any, Any], None]) -> None:
    """取消注册全局配置变更回调"""
    with _config_callbacks_lock:
        if callback in _config_callbacks:
            _config_callbacks.remove(callback)


def notify_config_change(old: Any, new: Any) -> None:
    """通知所有已注册的回调"""
    with _config_callbacks_lock:
        callbacks = list(_config_callbacks)
    for cb in callbacks:
        try:
            cb(old, new)
        except (TypeError, ValueError, AttributeError) as e:
            _logger.warning("config change callback failed: %s", e)


class ConfigWatch:
    """配置文件监听器（自动热更新）"""

    def __init__(
        self,
        config_cls,
        file_path: str,
        poll_interval: float = 5.0,
        debounce_seconds: float = 0.1,
    ):
        self.config_cls = config_cls
        self.file_path = file_path
        self.poll_interval = poll_interval
        self.debounce_seconds = debounce_seconds
        self._listeners: List[Callable[[Any, Any], None]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_mtime = self._file_mtime()
        self.config = self._load()

    def _load(self) -> Any:
        """加载当前配置"""
        try:
            return self.config_cls(**ConfigLoader.from_file(self.file_path))
        except (OSError, ValueError, TypeError) as e:
            _logger.warning("config load failed: %s", e)
            return self.config_cls()

    def _file_mtime(self) -> Optional[float]:
        """获取配置文件修改时间"""
        if os.path.exists(self.file_path):
            return os.path.getmtime(self.file_path)
        return None

    def on_change(self, callback: Callable[[Any, Any], None]) -> None:
        """注册本地配置变更回调"""
        self._listeners.append(callback)

    def start(self) -> None:
        """启动监听（后台线程）"""
        if self._running:
            return
        self._running = True
        self._last_mtime = self._file_mtime()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="config-watch"
        )
        self._thread.start()

    def stop(self) -> None:
        """停止监听"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _poll_loop(self) -> None:
        """轮询配置变更"""
        while self._running:
            try:
                self.check_once()
            except OSError as e:
                _logger.warning("config watch error: %s", e)
            time.sleep(self.poll_interval)

    def check_once(self) -> bool:
        """检查一次配置变更（手动触发或轮询调用）"""
        current_mtime = self._file_mtime()
        if current_mtime is None or current_mtime == self._last_mtime:
            return False

        # 去抖：避免短时间多次保存触发多次 reload
        time.sleep(self.debounce_seconds)
        if current_mtime != self._file_mtime():
            return False

        self._last_mtime = current_mtime
        old_config = self.config
        new_config = self._load()
        self.config = new_config

        # 通知本地监听器
        for callback in self._listeners:
            try:
                callback(old_config, new_config)
            except (TypeError, ValueError, AttributeError) as e:
                _logger.warning("local config callback failed: %s", e)

        # 通知全局监听器
        notify_config_change(old_config, new_config)
        return True

    def reload(self) -> Any:
        """手动重新加载配置"""
        self.check_once()
        return self.config


def start_global_hot_reload(
    config_cls,
    file_path: str,
    poll_interval: float = 5.0,
) -> ConfigWatch:
    """启动全局配置热更新（框架级入口）"""
    global _global_config_watcher
    if _global_config_watcher is not None:
        _logger.warning("global config watcher already running")
        return _global_config_watcher

    watcher = ConfigWatch(
        config_cls=config_cls,
        file_path=file_path,
        poll_interval=poll_interval,
    )
    # 自动接入 Components.notify_config_change
    try:
        from agentorchestra.components import Components
        watcher.on_change(Components.notify_config_change)
    except ImportError:
        pass
    watcher.start()
    _global_config_watcher = watcher
    return watcher


def stop_global_hot_reload() -> None:
    """停止全局配置热更新"""
    global _global_config_watcher
    if _global_config_watcher is not None:
        _global_config_watcher.stop()
        _global_config_watcher = None