"""scheduler - 图执行器（拓扑排序 + async 派发 + 条件路由 + 回环计数）（M2）。

执行语义：
- 所有消息经 Inbox 落库（store 配置时）→ 7 天可回溯 + 投递回执
- DAG 驱动 + 有界回环（节点 iteration 计数，达 max_iterations 转告警不再派发）
- 条件边：NodeOutput.route 与边 when 匹配才激活；无条件边总是激活
- 节点异常：on_node_error 回调 + status=partially_failed
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from ..state.backends.memory_backend import InMemoryCheckpointStore
from .events import NodeEvent, NodeEventType
from .graph import NodeContext, NodeOutput
from .inbox import Inbox

if TYPE_CHECKING:
    from ..state.checkpoint import CheckpointStore


class GraphScheduler:
    """图执行器。"""

    def __init__(
        self,
        store: Optional["CheckpointStore"] = None,
        max_iterations: int = 3,
        message_ttl_seconds: int = 604800,
    ):
        # 无 store → in-memory store（同样走 Inbox API）
        self.store = store if store is not None else InMemoryCheckpointStore()
        self.max_iterations = max_iterations
        self.message_ttl_seconds = message_ttl_seconds

    async def execute(
        self,
        graph: Any,
        initial_message: Dict[str, Any],
        thread_id: str,
        entry_node: Optional[str] = None,
        on_node_error: Optional[Callable[[NodeEvent], Any]] = None,
        on_delivery_failed: Optional[Callable[[Any], Any]] = None,
    ) -> Any:
        """执行图。返回 GraphResult（见 graph.py）。"""
        errors = graph.validate()
        if errors:
            raise ValueError(f"图配置错误: {errors}")

        from .graph import GraphResult

        graph_id = f"graph-{uuid.uuid4().hex[:12]}"
        inbox = Inbox(self.store, default_ttl_seconds=self.message_ttl_seconds)

        result = GraphResult(status="completed", graph_id=graph_id,
                             thread_id=thread_id)

        # 有界回环计数
        iteration: Dict[str, int] = {}

        def emit(ev: NodeEvent) -> None:
            """记录事件并触发 on_node_error 回调（兼容协程）。"""
            result.events.append(ev)
            if ev.event_type == NodeEventType.NODE_ERROR and on_node_error:
                try:
                    out = on_node_error(ev)
                    if asyncio.iscoroutine(out):
                        loop = asyncio.get_event_loop()
                        loop.create_task(out)
                except Exception:
                    pass

        # 入口节点：显式 entry_node → 单入口；否则启动所有无入边根节点（支持多入口并行）
        entries = self._find_entries(graph) if entry_node is None else [entry_node]

        # 初始消息入队（多入口各自收到初始消息）
        for entry in entries:
            await inbox.send(graph_id, thread_id, entry, dict(initial_message),
                             from_node=None)

        processed_msg_ids: set[str] = set()

        # 主循环：poll queued → 处理 → 路由下游（入队）
        safety = 0
        while safety < 10000:
            safety += 1
            messages = await inbox.poll(thread_id)
            # 过滤本轮已处理（poll 会返回所有 queued，但处理中会标 delivered/acked）
            todo = [m for m in messages if m.msg_id not in processed_msg_ids]
            if not todo:
                break
            for msg in todo:
                await self._process_one(
                    graph=graph, msg=msg, inbox=inbox, iteration=iteration,
                    result=result, emit=emit, thread_id=thread_id,
                    graph_id=graph_id, on_delivery_failed=on_delivery_failed,
                )
                processed_msg_ids.add(msg.msg_id)

        result.iteration_count = max(iteration.values()) if iteration else 0
        return result

    async def _process_one(self, graph: Any, msg: Any, inbox: Inbox,
                           iteration: Dict[str, int], result: Any, emit: Any,
                           thread_id: str, graph_id: str,
                           on_delivery_failed: Optional[Callable[[Any], Any]]) -> None:
        """处理单条消息：执行节点 + 路由下游。"""
        node_name = msg.to_node
        node = graph.get_node(node_name)
        if node is None:
            result.errors.append({"node": node_name, "error": "未知节点"})
            return

        # 有界回环：消息 from_node 非 None 且目标已超次数 → 跳过
        it = iteration.get(node_name, 0)
        is_loopback = msg.from_node is not None
        if is_loopback and it >= self.max_iterations:
            ev = NodeEvent(NodeEventType.NODE_SKIPPED, node_name, graph_id,
                           thread_id, msg.msg_id,
                           error=f"回环迭代上限({self.max_iterations})")
            emit(ev)
            result.errors.append({
                "node": node_name,
                "error": f"iteration limit exceeded: {node_name}",
            })
            await inbox.ack(msg.msg_id, msg.ack_token, "acked")
            return

        # 投递回执（delivered）
        ack_token = await inbox.mark_delivered(msg.msg_id)

        # 执行节点
        ctx = NodeContext(graph_id=graph_id, thread_id=thread_id,
                          message_id=msg.msg_id, from_node=msg.from_node,
                          store=self.store, inbox=inbox,
                          iteration=dict(iteration), emit=emit)
        iteration[node_name] = it + 1
        ctx.iteration = dict(iteration)

        emit(NodeEvent(NodeEventType.NODE_START, node_name, graph_id,
                       thread_id, msg.msg_id))

        try:
            output = await node.run(msg.content, ctx)
        except Exception as e:
            output = NodeOutput.fail(str(e))

        if output is None:
            output = NodeOutput.ok()

        if output.error:
            result.status = "partially_failed"
            result.node_results[node_name] = {"error": output.error}
            result.errors.append({"node": node_name, "error": output.error})
            emit(NodeEvent(NodeEventType.NODE_ERROR, node_name, graph_id,
                           thread_id, msg.msg_id, error=output.error))
            await inbox.ack(msg.msg_id, ack_token, "acked")
            return

        result.node_results[node_name] = output.result
        emit(NodeEvent(NodeEventType.NODE_FINISH, node_name, graph_id,
                       thread_id, msg.msg_id, data={"result": output.result}))

        # 路由下游
        await self._route_downstream(graph, node_name, output, thread_id,
                                     graph_id, inbox, iteration, result)
        await inbox.ack(msg.msg_id, ack_token, "acked")

    async def _route_downstream(
        self, graph: Any, node_name: str, output: NodeOutput,
        thread_id: str, graph_id: str, inbox: Inbox,
        iteration: Dict[str, int], result: Any,
    ) -> None:
        """按条件边把输出路由到下游。"""
        route = output.route
        for edge in graph.outgoing(node_name):
            # 条件边匹配
            if edge.when is not None and edge.when != route:
                continue

            target_it = iteration.get(edge.target, 0)
            is_loopback = target_it >= self.max_iterations and target_it > 0
            if is_loopback:
                result.errors.append({
                    "node": edge.target,
                    "error": f"max_iterations({self.max_iterations}) reached on "
                             f"loopback {node_name}->{edge.target}",
                })
                continue

            content = {
                "task": output.result,
                **(output.data or {}),
            }
            await inbox.send(graph_id, thread_id, edge.target, content,
                             from_node=node_name, condition=edge.when)

    def _find_entries(self, graph: Any) -> list:
        """找所有无入边的节点（入口）。"""
        has_in: set = set()
        for node in graph.nodes():
            for e in graph.outgoing(node):
                has_in.add(e.target)
        entries = [n for n in graph.nodes() if n not in has_in]
        if not entries:
            raise ValueError("图中无入口节点（所有节点都有入边）")
        return entries


__all__ = ["GraphScheduler"]
