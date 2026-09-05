"""compensation - 补偿编排（M1 事务运行时）。

逆序补偿 + 重试 N 次 + 耗尽进 DLQ。roadmap §3.2『逆序 compensate + 重试 + DLQ』。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from .coordinator import TransactionCoordinator

logger = logging.getLogger("agentorchestra.tx.compensation")


class CompensationExecutor:
    """补偿执行器。"""

    def __init__(
        self,
        coordinator: "TransactionCoordinator",
        max_attempts: int = 3,
        backoff: float = 0.1,
    ):
        self.coordinator = coordinator
        self.max_attempts = max_attempts
        self.backoff = backoff

    async def compensate(
        self,
        ctx: "TxContext",
        completed: List[str],
        completed_params: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """逆序补偿已完成动作。

        Args:
            ctx: TxContext（传递给补偿函数，使补偿逻辑可访问事务状态/WAL/锁等）。
            completed: 已完成动作名列表。
            completed_params: 每个动作的参数（来自执行阶段）。

        Returns:
            {"compensated": [...], "failed": [...], "dlq": [...]}
        """
        compensated: List[str] = []
        failed: List[Dict[str, str]] = []
        dlq: List[str] = []

        tx_id = ctx.tx_id

        for name in reversed(completed):
            action = self.coordinator.get_action(name)
            if action is None:
                failed.append({"action": name, "error": "动作未注册"})
                continue
            if action.compensate_fn is None:
                failed.append({"action": name, "error": "动作无补偿函数"})
                continue

            params = completed_params.get(name, {})
            attempts = 0
            last_error = ""
            while attempts < self.max_attempts:
                attempts += 1
                try:
                    result = action.compensate_fn(params, ctx)  #传递 ctx 而非 None
                    if asyncio.iscoroutine(result):
                        await result
                    compensated.append(name)
                    break
                except Exception as e:
                    last_error = str(e)
                    logger.warning(
                        f"tx {tx_id} 补偿 '{name}' 失败（{attempts}/{self.max_attempts}）: {last_error}"
                    )
                    if attempts < self.max_attempts:
                        await asyncio.sleep(self.backoff * attempts)
            else:
                # 耗尽重试 → DLQ
                failed.append({"action": name, "error": last_error})
                if self.coordinator.dlq is not None:
                    await self.coordinator.dlq.enqueue(
                        tx_id=tx_id,
                        action_name=name,
                        error=last_error,
                        attempts=attempts,
                    )
                    dlq.append(name)

        return {"compensated": compensated, "failed": failed, "dlq": dlq}
