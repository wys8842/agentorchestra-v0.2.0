"""graph - Graph / Node / Edge 声明式 + 拓扑校验（M2 图通信）。

拓扑：DAG + 有界回环（max_iterations），回边在 scheduler 用计数保证终止。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .events import NodeEvent

if TYPE_CHECKING:
    from ..state.checkpoint import CheckpointStore
    from .inbox import Inbox


@dataclass
class Edge:
    """图边。

    Attributes:
        source: 源节点名
        target: 目标节点名
        when: 条件标签。None = 无条件（总是激活）；否则消息产物需含该标签才激活。
    """

    source: str
    target: str
    when: Optional[str] = None


@dataclass
class NodeContext:
    """节点执行上下文。"""

    graph_id: str
    thread_id: str
    message_id: str
    from_node: Optional[str]
    store: Optional["CheckpointStore"] = None
    inbox: Optional["Inbox"] = None
    iteration: Dict[str, int] = field(default_factory=dict)  # 节点名 -> 已执行次数
    emit: Optional[Callable[[NodeEvent], None]] = None

    def node_iteration(self, name: str) -> int:
        """读取指定节点已执行次数（默认 0）。"""
        return self.iteration.get(name, 0)


@dataclass
class NodeOutput:
    """节点输出。

    Attributes:
        result: 主结果（任意 JSON 可序列化）
        route: 条件路由标签（如 "approved"/"rejected"/"done"）。None = 无条件激活下游。
        error: 可选错误信息（节点异常时由 scheduler 捕获，不通过 NodeOutput）
        data: 附加数据（透传给下游消息 content）
    """

    result: Any = None
    route: Optional[str] = None
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, result: Any = None, route: Optional[str] = None,
           **data: Any) -> "NodeOutput":
        """构造成功输出（kwargs 进入 data）。"""
        return cls(result=result, route=route, data=data)

    @classmethod
    def fail(cls, error: str) -> "NodeOutput":
        """构造失败输出（仅含错误信息）。"""
        return cls(error=error)


class Node(ABC):
    """图节点基类。

    子类实现 run()。见 AgentNode / RouterNode / MergeNode（nodes.py）。
    """

    name: str

    @abstractmethod
    async def run(self, message: Dict[str, Any], ctx: NodeContext) -> NodeOutput:
        """执行节点逻辑。

        Args:
            message: 收到的消息内容（dict）
            ctx: 执行上下文

        Returns:
            NodeOutput
        """


@dataclass
class GraphResult:
    """图执行结果。"""

    status: str  # completed | failed | partially_failed
    graph_id: str
    thread_id: str
    node_results: Dict[str, Any] = field(default_factory=dict)
    events: List[NodeEvent] = field(default_factory=list)
    iteration_count: int = 0
    messages: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可传输字典（略去 events/messages）。"""
        return {
            "status": self.status,
            "graph_id": self.graph_id,
            "thread_id": self.thread_id,
            "node_results": self.node_results,
            "iteration_count": self.iteration_count,
            "errors": self.errors,
        }


class Graph:
    """图（DAG + 有界回环）。

    支持子图嵌套：subgraph 节点内部包含完整子图。

    用法：
        g = Graph(store=checkpoint_store)
        g.add_node("coder", AgentNode(agent_factory=coder_factory))
        g.add_node("reviewer", AgentNode(agent_factory=reviewer_factory))
        g.add_edge("coder", "reviewer", when="done")

        # 子图
        sub = Graph().add_node("inner", NodeImpl()).add_edge("inner", "out")
        g.add_subgraph("my_sub", sub, entry="inner", exits={"out": "next"})

        result = await g.run({"task": "..."}, thread_id="order-1")
    """

    def __init__(
        self,
        store: Optional["CheckpointStore"] = None,
        max_iterations: int = 3,
        message_ttl_seconds: int = 604800,
    ):
        self.store = store
        self.max_iterations = max_iterations
        self.message_ttl_seconds = message_ttl_seconds
        self._nodes: Dict[str, Node] = {}
        self._edges: Dict[str, List[Edge]] = {}  # source -> out-edges
        self._subgraphs: Dict[str, "SubgraphNode"] = {}  # name -> SubgraphNode
        self._subgraph_aliases: Dict[str, str] = {}  # alias -> subgraph name

    # ---------------- 构建 ----------------

    def add_node(self, name: str, node: Node) -> "Graph":
        """添加节点。"""
        node.name = name
        self._nodes[name] = node
        self._edges.setdefault(name, [])
        return self

    def add_edge(self, source: str, target: str,
                 when: Optional[str] = None) -> "Graph":
        """添加边。when=None 表示无条件。"""
        if source not in self._nodes and source not in self._subgraphs:
            raise ValueError(f"未知源节点: {source}")
        if target not in self._nodes and target not in self._subgraphs:
            raise ValueError(f"未知目标节点: {target}")
        self._edges.setdefault(source, []).append(Edge(source, target, when))
        return self

    def add_subgraph(
        self,
        name: str,
        subgraph: "Graph",
        entry: str,
        exits: Dict[str, str],
    ) -> "Graph":
        """添加子图

        Args:
            name: 子图对外暴露的节点名
            subgraph: 子图实例
            entry: 子图入口节点名（接收外部输入）
            exits: 子图出口映射 {子图内部节点 -> 外部目标节点}
        """
        from .nodes import SubgraphNode

        # 内部节点名前缀化，避免与外部冲突
        prefix = f"{name}."
        prefixed_nodes = {}
        prefixed_edges = {}
        for n in subgraph._nodes:
            prefixed_nodes[prefix + n] = subgraph._nodes[n]

        # 转换边
        for src, edges in subgraph._edges.items():
            src_pref = prefix + src
            for e in edges:
                tgt_pref = prefix + e.target
                prefixed_edges.setdefault(src_pref, []).append(
                    Edge(src_pref, tgt_pref, e.when)
                )

        # 创建子图节点
        sub_node = SubgraphNode(
            name=name,
            entry=prefix + entry,
            nodes=prefixed_nodes,
            edges=prefixed_edges,
            exits={prefix + k: v for k, v in exits.items()},
        )
        self._subgraphs[name] = sub_node
        self._edges.setdefault(name, [])
        # 让外部节点可以引用子图
        self._subgraph_aliases[name] = name
        # 在子图名上注册为一个虚拟节点
        return self

    def nodes(self) -> List[str]:
        """返回全部节点名（包括子图）。"""
        all_nodes = list(self._nodes.keys())
        all_nodes.extend(self._subgraphs.keys())
        return all_nodes

    def get_node(self, name: str) -> Optional[Node]:
        """按名取节点（优先普通节点，其次子图）。"""
        node = self._nodes.get(name)
        if node is not None:
            return node
        return self._subgraphs.get(name)

    def outgoing(self, name: str) -> List[Edge]:
        """返回指定节点的全部出边。"""
        return list(self._edges.get(name, []))

    def incoming(self, name: str) -> List[Edge]:
        """返回指定节点的全部入边（O(n)，小图无影响）。"""
        result = []
        for src, edges in self._edges.items():
            for e in edges:
                if e.target == name:
                    result.append(e)
        return result

    def entry_nodes(self) -> List[str]:
        """找所有无入边的节点（入口节点）。"""
        has_in = set()
        for edges in self._edges.values():
            for e in edges:
                has_in.add(e.target)
        return [n for n in self.nodes() if n not in has_in]

    # ---------------- 拓扑校验 ----------------

    def validate(self) -> List[str]:
        """校验：节点存在、无自环、未知边。返回错误列表。"""
        errors: List[str] = []
        all_nodes = self.nodes()
        for src, edges in self._edges.items():
            if src not in all_nodes:
                errors.append(f"未知源节点: {src}")
            for e in edges:
                if e.target not in all_nodes:
                    errors.append(f"边 {e.source}->{e.target} 目标节点未知")
                if e.source == e.target:
                    errors.append(f"自环不允许: {e.source}->{e.target}")
        return errors

    def has_cycle(self) -> bool:
        """检测是否有环（DFS）"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {n: WHITE for n in self.nodes()}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for e in self.outgoing(node):
                if e.target not in color:
                    continue
                if color[e.target] == GRAY:
                    return True
                if color[e.target] == WHITE and dfs(e.target):
                    return True
            color[node] = BLACK
            return False

        return any(dfs(n) for n in self.nodes() if color[n] == WHITE)

    # ---------------- 模板图（克隆） ----------------

    def clone(self, rename: Optional[Dict[str, str]] = None) -> "Graph":
        """克隆图（用于模板图实例化）

        Args:
            rename: 节点重命名映射
        """
        new_graph = Graph(
            store=self.store,
            max_iterations=self.max_iterations,
            message_ttl_seconds=self.message_ttl_seconds,
        )
        rn = rename or {}

        for name, node in self._nodes.items():
            new_name = rn.get(name, name)
            new_graph.add_node(new_name, node)

        for src, edges in self._edges.items():
            src_rn = rn.get(src, src)
            for e in edges:
                tgt_rn = rn.get(e.target, e.target)
                new_graph.add_edge(src_rn, tgt_rn, e.when)

        return new_graph

    # ---------------- 执行 ----------------

    async def run(
        self,
        initial_message: Dict[str, Any],
        thread_id: str,
        entry_node: Optional[str] = None,
        on_node_error: Optional[Callable[[NodeEvent], Any]] = None,
        on_delivery_failed: Optional[Callable[[Any], Any]] = None,
    ) -> GraphResult:
        """运行图（async 核心）。"""
        from .scheduler import GraphScheduler

        scheduler = GraphScheduler(
            store=self.store,
            max_iterations=self.max_iterations,
            message_ttl_seconds=self.message_ttl_seconds,
        )
        return await scheduler.execute(
            graph=self,
            initial_message=initial_message,
            thread_id=thread_id,
            entry_node=entry_node,
            on_node_error=on_node_error,
            on_delivery_failed=on_delivery_failed,
        )

    def __repr__(self) -> str:
        return f"Graph(nodes={list(self._nodes.keys())}, edges={sum(len(v) for v in self._edges.values())})"


__all__ = ["Edge", "NodeContext", "NodeOutput", "Node", "Graph", "GraphResult"]
