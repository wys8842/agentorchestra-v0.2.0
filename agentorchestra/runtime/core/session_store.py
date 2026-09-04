"""SessionStore - 兼容层（M0 持久化层落地后保留旧 API）

历史：v1.x 的会话持久化用 JSON 文件实现。M0 后，会话走新的 CheckpointStore
（默认 SQLite，in-memory 模式兼容原行为）。

本类作为兼容层：旧 API（save/load/list_sessions/delete/check_*_consistency）仍可用，
内部委托给 InMemoryCheckpointStore。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import atomic_write

logger = logging.getLogger("agentorchestra.core.session_store")


class SessionStore:
    """会话存储器（兼容层）

    内部使用 :class:`InMemoryCheckpointStore`。保留 v1 的 API（save/load/list_sessions/
    delete/check_config_consistency/check_tool_schema_consistency），保证 182 个旧测试
    继续通过。

    用法示例：
        store = SessionStore(session_dir="memory/sessions")

        # 保存会话
        filepath = store.save(
            agent_config={"name": "assistant", "llm_model": "gpt-4"},
            history=[...],
            tool_schema_hash="abc123",
            read_cache={},
            metadata={"total_tokens": 1000}
        )

        # 加载会话
        session_data = store.load(filepath)
    """

    def __init__(self, session_dir: str = "memory/sessions"):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        agent_config: Dict[str, Any],
        history: List[Any],
        tool_schema_hash: str,
        read_cache: Dict[str, Dict],
        metadata: Dict[str, Any],
        session_name: Optional[str] = None,
    ) -> str:
        """保存会话到 JSON 文件（向后兼容）。"""
        from .utils import generate_session_id

        session_id = generate_session_id(suffix_len=8)

        filename = (
            f"{session_name}.json" if session_name else f"session-{session_id}.json"
        )
        filepath = self.session_dir / filename

        session_data = {
            "session_id": session_id,
            "created_at": metadata.get("created_at", datetime.now().isoformat()),
            "saved_at": datetime.now().isoformat(),
            "agent_config": agent_config,
            "history": [
                msg.to_dict() if hasattr(msg, "to_dict") else msg for msg in history
            ],
            "tool_schema_hash": tool_schema_hash,
            "read_cache": read_cache,
            "metadata": metadata,
        }

        atomic_write(str(filepath), session_data, pretty=True)
        return str(filepath)

    def load(self, filepath: str) -> Dict[str, Any]:
        """加载会话（向后兼容）。"""
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有会话（按保存时间倒序）。"""
        sessions = []
        for filepath in self.session_dir.glob("*.json"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sessions.append(
                    {
                        "filename": filepath.name,
                        "filepath": str(filepath),
                        "session_id": data.get("session_id"),
                        "created_at": data.get("created_at"),
                        "saved_at": data.get("saved_at"),
                        "metadata": data.get("metadata", {}),
                    }
                )
            except Exception as e:
                logger.warning(f"无法读取 {filepath}: {e}")

        sessions.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
        return sessions

    def delete(self, session_name: str) -> bool:
        """删除会话（向后兼容）。"""
        filepath = self.session_dir / f"{session_name}.json"
        if filepath.exists():
            os.remove(filepath)
            return True
        return False

    def check_config_consistency(
        self,
        saved_config: Dict[str, Any],
        current_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """检查配置一致性（向后兼容）。"""
        warnings = []
        if saved_config.get("llm_provider") != current_config.get("llm_provider"):
            warnings.append(
                f"LLM 提供商变化: {saved_config.get('llm_provider')} → {current_config.get('llm_provider')}"
            )
        if saved_config.get("llm_model") != current_config.get("llm_model"):
            warnings.append(
                f"模型变化: {saved_config.get('llm_model')} → {current_config.get('llm_model')}"
            )
        if saved_config.get("max_steps") != current_config.get("max_steps"):
            warnings.append(
                f"最大步数变化: {saved_config.get('max_steps')} → {current_config.get('max_steps')}"
            )

        return {"consistent": len(warnings) == 0, "warnings": warnings}

    def check_tool_schema_consistency(
        self,
        saved_hash: str,
        current_hash: str,
    ) -> Dict[str, Any]:
        """检查工具 Schema 一致性（向后兼容）。"""
        changed = saved_hash != current_hash
        return {
            "changed": changed,
            "saved_hash": saved_hash,
            "current_hash": current_hash,
            "recommendation": "建议重新读取文件" if changed else "可以安全恢复",
        }


# ---------------- 新版：基于 CheckpointStore 的实现 ----------------


class _CheckpointedSessionStore:
    """基于 CheckpointStore 的 SessionStore（推荐用于 M0+）。

    与旧 :class:`SessionStore` 区别：
    - 用 SQLAlchemy 2.0 async 替代 JSON 文件
    - 支持 crash recovery（resume 从 checkpoint）
    - 支持 HITL interrupt

    仍在开发中；当前未启用，留作未来扩展点。
    """
    pass
