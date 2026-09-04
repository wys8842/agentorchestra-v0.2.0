"""cas - ObjectCAS（对象 version 读写/校验）（M3）。

对象内嵌 SYSTEM_FIELDS（version/created_tx/last_modified_tx）。
roadmap §5.2『每对象带 version, created_tx, last_modified_tx』
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ObjectCAS:
    """对象级 CAS（封装 SYSTEM_FIELDS）。"""

    SYSTEM_FIELDS = {"version", "created_tx", "last_modified_tx"}

    @staticmethod
    def version_of(obj: Optional[Dict[str, Any]]) -> int:
        """读取对象 version（无则视为 0）。"""
        if not obj:
            return 0
        return int(obj.get("version", 0) or 0)

    @staticmethod
    def init(obj: Dict[str, Any], tx_id: Optional[str] = None) -> Dict[str, Any]:
        """insert 时注入初始 SYSTEM_FIELDS。"""
        tx = tx_id or "none"
        obj["version"] = 1
        obj["created_tx"] = tx
        obj["last_modified_tx"] = tx
        return obj

    @staticmethod
    def bump(obj: Dict[str, Any], tx_id: Optional[str] = None) -> Dict[str, Any]:
        """update 成功后 version+1，更新 last_modified_tx。"""
        obj["version"] = int(obj.get("version", 0) or 0) + 1
        obj["last_modified_tx"] = tx_id or obj.get("last_modified_tx", "none")
        return obj

    @staticmethod
    def check(
        current: Dict[str, Any], expected_version: Optional[int]
    ) -> bool:
        """CAS 校验：expected_version 与当前 version 比对。

        expected_version=None（未要求）→ True。
        """
        if expected_version is None:
            return True
        return ObjectCAS.version_of(current) == expected_version

    @staticmethod
    def strip_system_fields(obj: Dict[str, Any]) -> Dict[str, Any]:
        """移除 SYSTEM_FIELDS（需要纯业务字段时用）。"""
        return {k: v for k, v in obj.items() if k not in ObjectCAS.SYSTEM_FIELDS}


__all__ = ["ObjectCAS"]
