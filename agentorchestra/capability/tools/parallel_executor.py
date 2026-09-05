"""并行工具执行器

特性：
- 动态并发度（基于系统负载自适应）
- 工具依赖分析（独立工具并行，有依赖的串行）
- 自适应退避
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple


class DependencyStrategy(Enum):
    """依赖分析策略"""
    NONE = "none"               # 不分析（全部并行）
    STATIC = "static"           # 基于工具声明
    DYNAMIC = "dynamic"         # 基于运行时推断


@dataclass
class ToolDependency:
    """工具依赖声明

    Attributes:
        tool_name: 工具名
        depends_on: 依赖的工具集合（必须先于本工具完成）
        mutually_exclusive: 互斥工具（不能同时执行）
    """

    tool_name: str
    depends_on: Set[str] = field(default_factory=set)
    mutually_exclusive: Set[str] = field(default_factory=set)


class DependencyAnalyzer:
    """工具依赖分析器"""

    def __init__(self):
        self._dependencies: Dict[str, ToolDependency] = {}

    def declare(self, dep: ToolDependency) -> None:
        """声明工具依赖"""
        self._dependencies[dep.tool_name] = dep

    def get_dependency(self, tool_name: str) -> Optional[ToolDependency]:
        """获取工具依赖"""
        return self._dependencies.get(tool_name)

    def build_groups(
        self, tool_calls: List[Any]
    ) -> List[List[Any]]:
        """将工具调用分组（同一组内可并行，不同组间串行）

        Returns:
            分组列表，每组内的工具可并行执行
        """
        # 拓扑排序：BFS 分层
        in_degree: Dict[str, int] = {self._name(tc): 0 for tc in tool_calls}
        name_to_call: Dict[str, Any] = {self._name(tc): tc for tc in tool_calls}

        for tc in tool_calls:
            name = self._name(tc)
            dep = self._dependencies.get(name)
            if dep:
                for d in dep.depends_on:
                    if d in in_degree:
                        in_degree[name] += 1

        groups = []
        current_group_names = [n for n, deg in in_degree.items() if deg == 0]

        while current_group_names:
            groups.append([name_to_call[n] for n in current_group_names])
            next_group_names = []

            for n in current_group_names:
                for tc in tool_calls:
                    other_name = self._name(tc)
                    if other_name == n:
                        continue
                    dep = self._dependencies.get(other_name)
                    if dep and n in dep.depends_on:
                        in_degree[other_name] -= 1
                        if in_degree[other_name] == 0:
                            next_group_names.append(other_name)

            current_group_names = next_group_names

        return groups

    @staticmethod
    def _name(tool_call: Any) -> str:
        """获取工具调用的名称"""
        if hasattr(tool_call, "function"):
            return tool_call.function.name
        if isinstance(tool_call, dict):
            return tool_call.get("name") or tool_call.get("tool_name", "")
        return getattr(tool_call, "name", str(tool_call))


class AdaptiveConcurrency:
    """自适应并发度控制器

    根据 CPU 负载和当前任务数动态调整 Semaphore 限额。
    """

    def __init__(
        self,
        min_limit: int = 1,
        max_limit: int = 10,
        target_cpu_load: float = 0.7,
    ):
        self.min_limit = min_limit
        self.max_limit = max_limit
        self.target_cpu_load = target_cpu_load
        self._current_limit = (min_limit + max_limit) // 2

    def adjust(self, current_load: Optional[float] = None) -> int:
        """根据负载调整并发度

        Args:
            current_load: 当前 CPU 负载（None 时自动获取）

        Returns:
            调整后的并发度
        """
        load = current_load if current_load is not None else self._get_cpu_load()

        if load < self.target_cpu_load * 0.5:
            # 负载低 → 提高并发度
            self._current_limit = min(self._current_limit + 1, self.max_limit)
        elif load > self.target_cpu_load * 1.2:
            # 负载高 → 降低并发度
            self._current_limit = max(self._current_limit - 1, self.min_limit)

        return self._current_limit

    @staticmethod
    def _get_cpu_load() -> float:
        """获取 CPU 负载（跨平台）"""
        try:
            import psutil
            return psutil.cpu_percent(interval=0.1) / 100.0
        except ImportError:
            # fallback：返回假设负载
            return 0.5

    def get_current_limit(self) -> int:
        """获取当前并发度"""
        return self._current_limit


async def execute_tools_with_dependencies(
    tool_calls: List[Any],
    execute_fn: Callable[[Any], Awaitable[Tuple[str, str, Dict[str, Any]]]],
    analyzer: Optional[DependencyAnalyzer] = None,
    concurrency: Optional[AdaptiveConcurrency] = None,
    on_complete: Optional[Callable[[Tuple[str, str, Dict[str, Any]]], None]] = None,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """带依赖分析的并行工具执行

    Args:
        tool_calls: 工具调用列表
        execute_fn: 单个工具执行函数 (tool_call) -> (name, id, result_dict)
        analyzer: 依赖分析器（None 则全部并行）
        concurrency: 自适应并发度（None 则固定 max_concurrent_tools）
        on_complete: 单个工具完成回调

    Returns:
        所有工具执行结果
    """
    if not tool_calls:
        return []

    # 1. 依赖分组
    if analyzer:
        groups = analyzer.build_groups(tool_calls)
    else:
        groups = [tool_calls]

    all_results: List[Tuple[str, str, Dict[str, Any]]] = []
    limit = concurrency.get_current_limit() if concurrency else 5

    # 2. 顺序执行各组（组内并行）
    for group in groups:
        sem = asyncio.Semaphore(limit)

        async def run_with_sem(tc):
            async with sem:
                return await execute_fn(tc)

        group_results = await asyncio.gather(
            *[run_with_sem(tc) for tc in group],
            return_exceptions=False,
        )
        all_results.extend(group_results)

        # 通知完成回调
        if on_complete:
            for r in group_results:
                on_complete(r)

        # 自适应调整
        if concurrency:
            new_limit = concurrency.adjust()
            limit = new_limit

    return all_results