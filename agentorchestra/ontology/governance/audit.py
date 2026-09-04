"""Audit - 审计（M3 WORM 增强）

记录谁在何时对什么资源执行了什么操作。

M3 后支持两种后端：
- 纯内存（默认，向后兼容）：list 存储，可 clear
- 持久化 WORM（可选 store backend）：append-only 到 CheckpointStore.audit_log；
  配 backend 后 clear() 只清内存不删 DB 行（保证 WORM）
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agentorchestra.ontology.governance.audit")


class AuditManager:
    """审计管理器"""

    def __init__(self):
        self._log: List[Dict[str, Any]] = []
        self._backend: Optional[Any] = None  # M3：CheckpointStore（WORM append-only）

    def attach_backend(self, store: Any) -> None:
        """装配持久化 WORM backend。

        之后 log() 同时写内存（即时可查）与后端（异步 append）。
        clear() 只清内存，不删后端行（WORM）。
        """
        self._backend = store

    def log(self, principal: str, resource: str, action: str,
            detail: Optional[Dict[str, Any]] = None, success: bool = True) -> None:
        """记录审计。配 backend 时异步 append 到 audit_log（WORM）。"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "principal": principal,
            "resource": resource,
            "action": action,
            "detail": detail or {},
            "success": success,
        }
        self._log.append(entry)

        if self._backend is not None:
            try:
                self._async_append(principal, resource, action,
                                   detail or {}, success)
            except Exception as e:
                logger.warning(f"审计 WORM append 失败: {e}")

    def _async_append(self, principal, resource, action, detail, success) -> None:
        """异步 append 到后端（fire-and-forget）。"""
        from agentorchestra.orchestration.state.records import AuditEntry

        entry = AuditEntry(
            principal=principal, resource=resource, action=action,
            obj_id=detail.get("obj_id") if isinstance(detail, dict) else None,
            success=success, detail=detail,
        )

        async def _do():
            await self._backend.append_audit(entry)  # type: ignore[attr-defined]

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do())
        except RuntimeError:
            # 无运行中 loop（同步上下文）→ 尝试新建 loop 执行（桥接）
            try:
                asyncio.run(_do())
            except Exception as e:
                logger.warning(f"审计 WORM append 同步桥接失败: {e}")

    def query(self, principal: Optional[str] = None, resource: Optional[str] = None,
              limit: int = 100) -> List[Dict[str, Any]]:
        """按主体/资源过滤审计日志（最新在前）"""
        entries = self._log
        if principal:
            entries = [e for e in entries if e["principal"] == principal]
        if resource:
            entries = [e for e in entries if e["resource"] == resource]
        return list(reversed(entries))[:limit]

    def count(self) -> int:
        """返回审计记录条数"""
        return len(self._log)

    def clear(self) -> None:
        """清空内存审计日志（配 WORM 后端时不删 DB 行）"""
        self._log.clear()
