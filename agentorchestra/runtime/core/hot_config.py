"""配置热更新

提供：
- ConfigWatch: 监听配置文件变更，自动重新加载 Config
- 运行时更新回调（配置变化时通知）
"""

import os
import threading
import time
from typing import Any, Callable, List, Optional

from .config_loader import ConfigLoader


class ConfigWatch:
    """配置文件监听器（自动热更新）"""

    def __init__(self, config_cls, file_path: str,
                 poll_interval: float = 5.0):
        """初始化配置监听

        Args:
            config_cls: Config 类
            file_path: 配置文件路径
            poll_interval: 轮询间隔（秒）
        """
        self.config_cls = config_cls
        self.file_path = file_path
        self.poll_interval = poll_interval
        self._listeners: List[Callable[[Any, Any], None]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_mtime = self._file_mtime()
        self.config = self._load()

    # ==================== 加载 ====================

    def _load(self) -> Any:
        """加载当前配置"""
        try:
            return self.config_cls(**ConfigLoader.from_file(self.file_path))
        except Exception:
            return self.config_cls()

    def _file_mtime(self) -> Optional[float]:
        """获取配置文件修改时间"""
        if os.path.exists(self.file_path):
            return os.path.getmtime(self.file_path)
        return None

    # ==================== 监听 ====================

    def on_change(self, callback: Callable[[Any, Any], None]) -> None:
        """注册配置变更回调

        Args:
            callback: 回调函数 fn(old_config, new_config)
        """
        self._listeners.append(callback)

    def start(self) -> None:
        """启动监听（后台线程）"""
        if self._running:
            return
        self._running = True
        self._last_mtime = self._file_mtime()
        self._thread = threading.Thread(target=self._poll_loop,
                                        daemon=True, name="config-watch")
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
            except Exception:
                pass
            time.sleep(self.poll_interval)

    def check_once(self) -> bool:
        """检查一次配置变更（手动触发或轮询调用）

        Returns:
            配置是否发生变化
        """
        current_mtime = self._file_mtime()
        if current_mtime is None or current_mtime == self._last_mtime:
            return False

        self._last_mtime = current_mtime
        old_config = self.config
        new_config = self._load()
        self.config = new_config

        # 通知监听器
        for callback in self._listeners:
            try:
                callback(old_config, new_config)
            except Exception:
                pass
        return True

    def reload(self) -> Any:
        """手动重新加载配置"""
        self.check_once()
        return self.config
