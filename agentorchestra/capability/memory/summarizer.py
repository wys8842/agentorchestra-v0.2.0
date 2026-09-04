"""Summarizer - 会话总结 → 候选记忆条目

调 SymphonyLLM 一次（轻量），要求严格 JSON 输出：
    [
      {"type": "fact", "content": "...", "tags": "..., ...", "importance": 0.7},
      ...
    ]

失败/超时：返回空列表（不影响 Agent run 主流程）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agentorchestra.memory.summarizer")


@dataclass
class MemoryCandidate:
    """自动总结产出的候选记忆（无 id/source_*，待 remember() 补全）。"""

    type: str = "fact"
    content: str = ""
    tags: List[str] = field(default_factory=list)
    importance: float = 0.5


_JSON_BLOCK_RE = re.compile(r"\[.*\]", re.DOTALL)


def _safe_parse_json_array(text: str) -> Optional[List[Dict[str, Any]]]:
    """从 LLM 输出中尽力解析 JSON 数组。"""
    if not text:
        return None
    # 尝试直接解析
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    # 尝试提取首个 [...] 块
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    snippet = m.group(0)
    try:
        parsed = json.loads(snippet)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        return None
    return None


class Summarizer:
    """会话历史 → 候选记忆条目。

    使用：
        s = Summarizer(llm)
        cands = await s.extract(input_text, history_messages, result_text)
    """

    SYSTEM_PROMPT = (
        "你是一个记忆提炼助手。请根据用户输入、本轮对话历史与最终回答，"
        "提炼出值得长期保存的记忆条目。"
        "严格返回 JSON 数组，每项结构为："
        "{\"type\": \"fact|preference|episode|procedure\", \"content\": \"...\", "
        "\"tags\": \"tag1, tag2\", \"importance\": 0.0~1.0}。"
        "只输出 JSON 数组，不要任何解释、不要代码代码块标记。"
    )

    def __init__(self, llm: Any, max_chars: int = 6000) -> None:
        self.llm = llm
        self.max_chars = max_chars

    async def extract(
        self,
        input_text: str,
        history: List[Any],
        result: str,
    ) -> List[MemoryCandidate]:
        """调一次轻量 LLM，返回候选记忆列表。失败返回 []。"""
        if self.llm is None:
            return []
        user_prompt = self._build_prompt(input_text, history, result)
        try:
            raw = await self._call_llm(user_prompt)
        except Exception as e:
            logger.warning(f"Summarizer 调用 LLM 失败: {e}")
            return []
        items = _safe_parse_json_array(raw)
        if not items:
            logger.debug("Summarizer 输出无法解析为 JSON 数组")
            return []

        out: List[MemoryCandidate] = []
        for item in items:
            try:
                cand = self._item_to_candidate(item)
            except Exception as e:
                logger.debug(f"Summarizer 单条解析失败: {e}")
                continue
            if cand and cand.content:
                out.append(cand)
        return out

    def _build_prompt(self, input_text: str, history: List[Any], result: str) -> str:
        lines: List[str] = []
        lines.append(f"用户输入：{input_text or ''}")
        lines.append("")
        lines.append("对话历史（最多保留 N 条）：")
        for msg in (history or [])[-10:]:
            role = getattr(msg, "role", "")
            content = getattr(msg, "content", str(msg))
            lines.append(f"- [{role}] {content}")
        lines.append("")
        lines.append(f"本轮最终回答：{result or ''}")
        lines.append("")
        lines.append("请输出 JSON 数组。")
        text = "\n".join(lines)
        if len(text) > self.max_chars:
            text = text[-self.max_chars:]
        return text

    def _item_to_candidate(self, item: Dict[str, Any]) -> Optional[MemoryCandidate]:
        if not isinstance(item, dict):
            return None
        content = str(item.get("content", "") or "").strip()
        if not content:
            return None
        type_str = str(item.get("type", "fact") or "fact")
        tags_raw = item.get("tags", "") or []
        if isinstance(tags_raw, str):
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        elif isinstance(tags_raw, list):
            tags = [str(t).strip() for t in tags_raw if str(t).strip()]
        else:
            tags = []
        try:
            importance = float(item.get("importance", 0.5) or 0.0)
        except (TypeError, ValueError):
            importance = 0.5
        importance = max(0.0, min(1.0, importance))
        return MemoryCandidate(
            type=type_str,
            content=content,
            tags=tags,
            importance=importance,
        )

    async def _call_llm(self, user_prompt: str) -> str:
        """调 LLM 的同步 ainvoke，返回文本。"""
        llm = self.llm
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        # 优先 ainvoke；无 ainvoke 时退到 invoke
        if hasattr(llm, "ainvoke"):
            resp = await llm.ainvoke(messages, temperature=0.2)
            return getattr(resp, "content", str(resp))
        if hasattr(llm, "invoke"):
            resp = llm.invoke(messages, temperature=0.2)
            return getattr(resp, "content", str(resp))
        raise RuntimeError("LLM 缺少 invoke/ainvoke 方法")
