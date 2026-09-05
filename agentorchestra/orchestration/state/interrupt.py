"""Interrupt - HITL（Human-in-the-Loop）中断与恢复。

Agent 主动发起中断 → 业务侧 resume(token, response) → 注入 response 继续执行。

InterruptResumer 类，监听 resolved interrupt，
自动回调注册的 handler 并传递 response；提供 in-process 异步 Resumer 与轮询 API。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger("agentorchestra.state.interrupt")


class InterruptStatus(str, Enum):
    """中断状态。"""

    PENDING = "pending"
    RESUMED = "resumed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class Interrupt:
    """一个中断请求。

    Attributes:
        token: UUIDv4，全局唯一
        thread_id: 所属 thread
        checkpoint_id: 中断时所在 checkpoint
        reason: 中断原因（如"需要审批"）
        payload: 任意 JSON（业务侧用于决策）
        status: 当前状态
        response: resume 时的响应
        created_at: 创建时间
        resolved_at: 解决时间
    """

    token: str
    thread_id: str
    checkpoint_id: str
    reason: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: InterruptStatus = InterruptStatus.PENDING
    response: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=datetime.now)
    resolved_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（状态/时间为可读字符串）。"""
        return {
            "token": self.token,
            "thread_id": self.thread_id,
            "checkpoint_id": self.checkpoint_id,
            "reason": self.reason,
            "payload": self.payload,
            "status": self.status.value,
            "response": self.response,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class InterruptPending(Exception):
    """Agent 因等待外部输入而暂停。

    业务侧捕获后调用 `agent.resume_with(token, response)` 恢复。
    """

    def __init__(self, token: str, reason: str, payload: Dict[str, Any]):
        super().__init__(f"中断待处理 token={token} reason={reason}")
        self.token = token
        self.reason = reason
        self.payload = payload




# 回调签名：async def handler(token, response, interrupt) -> None
InterruptHandler = Callable[[str, Dict[str, Any], Interrupt], Awaitable[None]]


class InterruptResumer:
    """异步消费 resolved interrupt 的 Resumer。

    用法：
        resumer = InterruptResumer(store)
        resumer.register_handler("approve_payment", my_handler)
        await resumer.start()  # 启动后台轮询任务
        ...
        await resumer.stop()


    Resumer 主动轮询 RESUMED 状态的 interrupt 并触发对应 handler。


    避免 handler bug 导致 interrupt 永远卡在 RESUMED 状态。
    """

    def __init__(
        self,
        store: Any,  # CheckpointStore
        poll_interval: float = 1.0,
        max_handler_failures: int = 3,
    ):
        self.store = store
        self.poll_interval = poll_interval
        self.max_handler_failures = max_handler_failures
        self._handlers: Dict[str, InterruptHandler] = {}
        self._task: Optional[asyncio.Task[Any]] = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._processed_tokens: set[str] = set()
        #：handler 失败计数器（token → 失败次数）
        self._handler_failures: Dict[str, int] = {}

    def register_handler(
        self, reason: str, handler: InterruptHandler
    ) -> None:
        """注册 interrupt 回调（按 reason 匹配）。"""
        self._handlers[reason] = handler

    def unregister_handler(self, reason: str) -> None:
        """注销回调。"""
        self._handlers.pop(reason, None)

    async def start(self) -> asyncio.Task:
        """启动后台轮询任务。"""
        if self._task is not None and not self._task.done():
            return self._task
        self._stop_event.clear()
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._loop(), name="interrupt-resumer")
        return self._task

    async def stop(self) -> None:
        """停止后台轮询任务。"""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        """后台轮询循环。"""
        while not self._stop_event.is_set():
            try:
                await self.poll_once()
            except Exception as e:  # noqa: BLE001
                logger.warning("InterruptResumer poll error: %s", e)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval,
                )
            except asyncio.TimeoutError:
                pass

    async def poll_once(self) -> int:
        """手动触发一轮：扫描 RESUMED interrupt 并触发 handler。返回处理数。

        业务可在主循环中定时调用，避免 start()/stop() 后台任务。
        """
        processed = 0
        # 列出所有 RESUMED interrupt
        try:
            # 约定 store.list_interrupts(status) 返回 List[Interrupt]
            items = await self.store.list_interrupts(status="resumed")  # type: ignore[attr-defined]
        except AttributeError:
            logger.debug("store 不支持 list_interrupts，跳过")
            return 0
        except Exception as e:  # noqa: BLE001
            logger.warning("list_interrupts 失败: %s", e)
            return 0

        for intr in items:
            if intr.token in self._processed_tokens:
                continue
            handler = self._handlers.get(intr.reason)
            if handler is None:
                logger.warning("interrupt %s 无对应 handler（reason=%s）", intr.token, intr.reason)
                self._processed_tokens.add(intr.token)
                continue
            try:
                await handler(intr.token, intr.response or {}, intr)
                processed += 1
                self._processed_tokens.add(intr.token)
                # 成功后清零失败计数
                self._handler_failures.pop(intr.token, None)
                logger.info("interrupt %s resumed by handler %s", intr.token, intr.reason)
            except Exception as e:  # noqa: BLE001
                #：失败计数；超过阈值重新置 PENDING 等待下次轮询
                self._handler_failures[intr.token] = self._handler_failures.get(intr.token, 0) + 1
                if self._handler_failures[intr.token] >= self.max_handler_failures:
                    logger.warning(
                        "interrupt %s handler 连续失败 %d 次（>=max=%d）；重新置 PENDING 等待下次轮询：%s",
                        intr.token,
                        self._handler_failures[intr.token],
                        self.max_handler_failures,
                        e,
                    )
                    # 不加入 processed_tokens，下次轮询再次尝试
                else:
                    logger.warning(
                        "handler for interrupt %s 异常（失败 %d/%d）：%s",
                        intr.token,
                        self._handler_failures[intr.token],
                        self.max_handler_failures,
                        e,
                    )
                    # 标记为已处理（避免下一轮立即重试，让运维有时间排查）
                    self._processed_tokens.add(intr.token)

        return processed


__all__ = [
    "Interrupt",
    "InterruptStatus",
    "InterruptPending",
    "InterruptResumer",
    "InterruptHandler",
]
