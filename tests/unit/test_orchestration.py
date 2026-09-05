"""Orchestration 模块单元测试"""

import pytest
import asyncio
from agentorchestra.orchestration.orch.graph import Graph, Node, Edge
from agentorchestra.orchestration.orch.scheduler import GraphScheduler
from agentorchestra.orchestration.orch.inbox import Inbox
from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore


class TestGraph:
    """图测试"""

    def test_graph_creation(self):
        """测试图创建"""
        graph = Graph()
        assert graph is not None

    def test_add_node(self):
        """测试添加节点"""
        graph = Graph()
        node = Node(id="node-1", run=lambda ctx, **kwargs: "result")
        graph.add_node(node)
        assert "node-1" in graph.nodes()

    def test_add_edge(self):
        """测试添加边"""
        graph = Graph()
        node1 = Node(id="node-1", run=lambda ctx, **kwargs: "result")
        node2 = Node(id="node-2", run=lambda ctx, **kwargs: "result2")
        graph.add_node(node1)
        graph.add_node(node2)
        graph.add_edge("node-1", "node-2")
        edges = list(graph.outgoing("node-1"))
        assert len(edges) > 0

    def test_validate_valid_graph(self):
        """测试有效图验证"""
        graph = Graph()
        node1 = Node(id="start", run=lambda ctx, **kwargs: "result")
        node2 = Node(id="end", run=lambda ctx, **kwargs: "result2")
        graph.add_node(node1)
        graph.add_node(node2)
        graph.add_edge("start", "end")
        errors = graph.validate()
        assert len(errors) == 0


@pytest.mark.asyncio
class TestScheduler:
    """调度器测试"""

    async def test_scheduler_creation(self):
        """测试调度器创建"""
        scheduler = GraphScheduler()
        assert scheduler is not None
        assert scheduler.max_iterations == 3

    async def test_scheduler_with_store(self):
        """测试带存储的调度器"""
        store = InMemoryCheckpointStore()
        await store.init()
        scheduler = GraphScheduler(store=store)
        assert scheduler.store is not None


@pytest.mark.asyncio
class TestInbox:
    """收件箱测试"""

    async def test_inbox_creation(self):
        """测试收件箱创建"""
        store = InMemoryCheckpointStore()
        await store.init()
        inbox = Inbox(store)
        assert inbox is not None

    async def test_send_and_poll(self):
        """测试发送和轮询"""
        store = InMemoryCheckpointStore()
        await store.init()
        inbox = Inbox(store)

        # 发送消息
        await inbox.send(
            graph_id="graph-1",
            thread_id="thread-1",
            to_node="node-1",
            content={"data": "test"}
        )

        # 轮询消息
        messages = await inbox.poll("thread-1")
        assert len(messages) > 0

    async def test_ack_message(self):
        """测试确认消息"""
        store = InMemoryCheckpointStore()
        await store.init()
        inbox = Inbox(store)

        await inbox.send("g-1", "t-1", "n-1", {"data": "test"})
        messages = await inbox.poll("t-1")
        if messages:
            msg = messages[0]
            await inbox.ack(msg.msg_id, msg.ack_token, "acked")
