"""sync - sync_transaction 桥接（M1 事务运行时）。

供旧同步调用方（TransactionManager.execute）使用：
- 无运行中事件循环 → asyncio.run 执行
- 已在事件循环内 → RuntimeError，提示调用方用 async with coordinator.transaction()
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


def run_sync(coro_factory: Callable[[], Any]) -> Any:
    """同步执行一个协程工厂函数。

    Args:
        coro_factory: 返回 awaitable 的可调用对象（无参）。

    Returns:
        协程结果

    Raises:
        RuntimeError: 已在运行中的事件循环内（应使用 await）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    raise RuntimeError(
        "sync_transaction 不能在运行中的事件循环内调用；请使用 "
        "`async with coordinator.transaction(...)`"
    )


__all__ = ["run_sync"]
