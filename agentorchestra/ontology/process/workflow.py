"""WorkflowEngine - 流程编排（多动作组合）

把多个 ActionType 编排成可执行的流程（workflow）：
- 顺序节点：按序执行动作
- 条件节点：根据条件选择分支
- 并行节点：并行执行多个动作
- 统一执行 + 结果收集 + 审计

节点类型：
- Step(node_id, action_name, params, depends_on)  顺序
- Condition(node_id, condition_fn, if_true, if_false)  条件
- Parallel(node_id, branches)  并行
"""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


class WorkflowNode:
    """流程节点基类"""

    def __init__(self, node_id: str, description: str = ""):
        self.node_id = node_id
        self.description = description


class StepNode(WorkflowNode):
    """顺序动作节点"""

    def __init__(self, node_id: str, action_name: str,
                 params: Optional[Dict[str, Any]] = None,
                 depends_on: Optional[List[str]] = None,
                 max_retries: int = 0,
                 description: str = ""):
        super().__init__(node_id, description)
        self.action_name = action_name
        self.params = params or {}
        self.depends_on = depends_on or []
        self.max_retries = max_retries


class ConditionNode(WorkflowNode):
    """条件分支节点"""

    def __init__(self, node_id: str, condition_fn: Callable,
                 if_true: str, if_false: Optional[str] = None,
                 description: str = ""):
        super().__init__(node_id, description)
        self.condition_fn = condition_fn  # fn(context) -> bool
        self.if_true = if_true           # 条件为真执行节点
        self.if_false = if_false         # 条件为假执行节点


class ParallelNode(WorkflowNode):
    """并行节点（多个子流程同时执行）"""

    def __init__(self, node_id: str, branches: List[str],
                 description: str = ""):
        super().__init__(node_id, description)
        self.branches = branches  # 并行执行的节点 id 列表


class Workflow:
    """工作流定义"""

    def __init__(self, name: str, description: str = "",
                 max_retries: int = 0):
        self.name = name
        self.description = description
        self.max_retries = max_retries
        self.nodes: Dict[str, WorkflowNode] = {}
        self.entry_points: List[str] = []  # 起始节点

    def add_node(self, node: WorkflowNode, entry: bool = False) -> "Workflow":
        """添加节点"""
        self.nodes[node.node_id] = node
        if entry:
            self.entry_points.append(node.node_id)
        return self

    def validate(self) -> List[str]:
        """校验流程：节点引用是否有效，无循环依赖"""
        errors = []

        # 检查节点引用有效性
        for node in self.nodes.values():
            for dep in self._get_references(node):
                if dep not in self.nodes:
                    errors.append(f"节点 '{node.node_id}' 引用不存在的节点 '{dep}'")

        # 检查循环依赖（使用 DFS）
        cycle = self._detect_cycle()
        if cycle:
            errors.append(f"工作流存在循环依赖: {' -> '.join(cycle)}")

        return errors

    def _detect_cycle(self) -> Optional[List[str]]:
        """检测循环依赖，返回循环路径（如果存在）

        只检测 StepNode 和 ParallelNode 的 depends_on 依赖（实际执行路径），
        ConditionNode 的条件分支不是真正的依赖关系，不参与循环检测。
        """
        # 构建后继图：node -> 依赖它的节点列表（只考虑真实依赖）
        successors: Dict[str, List[str]] = {nid: [] for nid in self.nodes}

        for nid, node in self.nodes.items():
            # 只考虑 StepNode 和 ParallelNode 的真实依赖
            if isinstance(node, (StepNode, ParallelNode)):
                deps = node.depends_on if isinstance(node, StepNode) else node.branches
                for dep in deps:
                    if dep in self.nodes:
                        successors[dep].append(nid)

        visited = set()
        rec_stack = set()
        path = []

        def dfs(node_id: str) -> Optional[List[str]]:
            """DFS 回溯检测环，命中时返回环路径"""
            visited.add(node_id)
            rec_stack.add(node_id)
            path.append(node_id)

            for succ in successors.get(node_id, []):
                if succ not in visited:
                    result = dfs(succ)
                    if result:
                        return result
                elif succ in rec_stack:
                    cycle_start = path.index(succ)
                    return path[cycle_start:] + [succ]

            path.pop()
            rec_stack.remove(node_id)
            return None

        for node_id in self.nodes:
            if node_id not in visited:
                result = dfs(node_id)
                if result:
                    return result

        return None

    def _get_references(self, node: WorkflowNode) -> List[str]:
        if isinstance(node, StepNode):
            return node.depends_on
        elif isinstance(node, ConditionNode):
            refs = [node.if_true]
            if node.if_false:
                refs.append(node.if_false)
            return refs
        elif isinstance(node, ParallelNode):
            return list(node.branches)
        return []


class WorkflowParamExpansionError(Exception):
    """Workflow 参数展开时检测到非法引用。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class WorkflowEngine:
    """工作流执行引擎"""

    def __init__(self, actions: Optional[Dict[str, Any]] = None):
        """初始化

        Args:
            actions: 动作注册表 {action_name: ActionType}
        """
        self.actions = actions or {}
        self._workflows: Dict[str, Workflow] = {}
        self._runs: List[Dict[str, Any]] = []  # 执行历史

    def register_action(self, action) -> None:
        """注册动作"""
        self.actions[action.api_name] = action

    def register_workflow(self, workflow: Workflow) -> None:
        """注册工作流"""
        errors = workflow.validate()
        if errors:
            raise ValueError(f"工作流 '{workflow.name}' 校验失败: {errors}")
        self._workflows[workflow.name] = workflow

    def get_workflow(self, name: str) -> Optional[Workflow]:
        """按名称获取工作流定义"""
        return self._workflows.get(name)

    def list_workflows(self) -> List[str]:
        """列出已注册工作流名"""
        return list(self._workflows.keys())

    # ==================== 执行 ====================

    def run(self, workflow_name: str,
            initial_params: Optional[Dict[str, Any]] = None,
            ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行工作流

        Args:
            workflow_name: 工作流名
            initial_params: 初始参数（注入所有步骤）
            ctx: 执行上下文

        Returns:
            {"success", "results": {node_id: result}, "errors", "started_at", "ended_at"}
        """
        workflow = self._workflows.get(workflow_name)
        if not workflow:
            raise ValueError(f"工作流不存在: {workflow_name}")

        ctx = ctx or {}
        context = {"params": dict(initial_params or {}), "results": {}}
        errors_list: List[str] = []
        run_record: Dict[str, Any] = {
            "workflow": workflow_name,
            "started_at": datetime.now().isoformat(),
            "results": {},
            "errors": errors_list,
            "success": False,
            "ended_at": "",
        }

        try:
            self._execute_topological(workflow, context, ctx, run_record)
        except Exception as e:
            errors_list.append(f"流程执行异常: {e}")

        run_record["ended_at"] = datetime.now().isoformat()
        run_record["success"] = not run_record["errors"]
        run_record["results"] = context["results"]
        self._runs.append(run_record)

        return run_record

    def _execute_topological(self, workflow: Workflow, context: Dict,
                             ctx: Dict, run_record: Dict) -> None:
        """拓扑顺序执行工作流

        从入口节点开始，执行后把"依赖已满足"的节点加入队列。
        depends_on 声明了节点的前置依赖。
        """
        # 依赖图：node_id -> 依赖它的节点列表（后继）
        successors: Dict[str, List[str]] = {nid: [] for nid in workflow.nodes}
        # 入度：node_id -> 未满足的前置依赖数
        indegree: Dict[str, int] = {nid: 0 for nid in workflow.nodes}

        for nid, node in workflow.nodes.items():
            if isinstance(node, StepNode):
                for dep in node.depends_on:
                    successors.setdefault(dep, []).append(nid)
                    indegree[nid] += 1
            elif isinstance(node, ParallelNode):
                pass

        # 初始队列：入口节点 或 无依赖节点
        ready = [nid for nid in workflow.nodes
                 if indegree[nid] == 0]
        # 优先入口节点
        for entry in workflow.entry_points:
            if entry in ready:
                ready.remove(entry)
                ready.insert(0, entry)

        executed: set = set()
        while ready:
            node_id = ready.pop(0)
            if node_id in executed:
                continue
            executed.add(node_id)

            node = workflow.nodes.get(node_id)  # type: ignore[assignment]
            if not node:
                continue

            if isinstance(node, StepNode):
                self._execute_step(node, context, ctx, run_record)
                # StepNode：释放后继（满足依赖的节点加入队列）
                for succ in successors.get(node_id, []):
                    if succ in executed:
                        continue
                    indegree[succ] -= 1
                    if indegree[succ] <= 0:
                        ready.append(succ)
            elif isinstance(node, ConditionNode):
                # 条件节点：根据条件决定后续，只释放选中的分支
                self._execute_condition_topological(
                    node, workflow, context, ctx, run_record,
                    successors, indegree, ready, executed)
            elif isinstance(node, ParallelNode):
                # 并行节点：释放所有分支（后续拓扑排序会处理）
                for branch in node.branches:
                    if branch in executed:
                        continue
                    # 手动将分支加入队列（并行执行）
                    ready.append(branch)

    def _execute_condition_topological(self, node: ConditionNode, workflow: Workflow,
                                       context: Dict, ctx: Dict, run_record: Dict,
                                       successors: Dict, indegree: Dict,
                                       ready: List[str], executed: set) -> None:
        """条件节点的拓扑执行：根据条件只释放选中的分支"""
        try:
            cond_result = node.condition_fn(context)
        except Exception as e:
            run_record["errors"].append(f"条件 '{node.node_id}' 异常: {e}")
            return

        next_node = node.if_true if cond_result else node.if_false
        if next_node:
            if next_node in indegree and next_node not in executed:
                indegree[next_node] -= 1
                if indegree[next_node] <= 0 and next_node not in ready:
                    ready.append(next_node)

    def _execute_step(self, node: StepNode, context: Dict, ctx: Dict,
                      run_record: Dict) -> None:
        """执行动作步骤"""
        action = self.actions.get(node.action_name)
        if not action:
            run_record["errors"].append(
                f"步骤 '{node.node_id}' 引用的动作不存在: {node.action_name}")
            return

        # 参数合并：初始参数 + 节点参数（含占位符展开）+ 前置结果
        params = dict(context["params"])
        #：注入 current_node_id 与 current_deps，供 _expand_params 白名单检查
        context["current_node_id"] = node.node_id
        context["current_deps"] = list(node.depends_on or [])
        try:
            params.update(self._expand_params(node.params, context))
        finally:
            # 清理临时字段（避免污染后续节点）
            context.pop("current_node_id", None)
            context.pop("current_deps", None)

        # 重试机制
        for attempt in range(node.max_retries + 1):
            result = action.execute(params, ctx)
            if result["success"]:
                context["results"][node.node_id] = result["result"]
                run_record["results"][node.node_id] = {
                    "action": node.action_name, "result": result["result"]}
                return
            if attempt < node.max_retries:
                continue
            run_record["errors"].append(
                f"步骤 '{node.node_id}' 失败: {result['errors']}")

    def _expand_params(self, params: Dict[str, Any], context: Dict) -> Dict[str, Any]:
        """展开参数中的占位符

        - "$key"：从初始参数（context["params"]）取值
        - "$node_id.key"：从前置节点结果（context["results"][node_id]）取值

          未声明依赖的 `$node_id.field` 引用视为安全违规，置 None 并记录告警。

        例：{"order_id": "$step_create.order_id"} （step_create 必须在 deps 中）
        """
        # 当前节点 id（由调用方注入 context["current_node_id"]）
        current_node_id = context.get("current_node_id")
        current_deps: set[str] = set(context.get("current_deps") or [])
        expanded = {}
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("$"):
                ref = value[1:]
                if "." in ref:
                    node_id, _, field = ref.partition(".")
                    #：白名单检查（仅允许访问 declared deps）
                    if current_node_id is not None and node_id != current_node_id \
                            and node_id not in current_deps:
                        #：strict=True 时抛异常而非静默 None
                        import logging
                        msg = (
                            f"参数 '{key}' 引用 '${node_id}.{field}' 未声明依赖"
                            f"（current deps={current_deps}）"
                        )
                        strict = context.get("strict_param_expansion", True)
                        if strict:
                            raise WorkflowParamExpansionError(msg)
                        logging.getLogger("agentorchestra.workflow").warning(msg + "；置 None")
                        expanded[key] = None
                        continue
                    node_result = context["results"].get(node_id, {})
                    if isinstance(node_result, dict):
                        expanded[key] = node_result.get(field)
                    else:
                        expanded[key] = None
                else:
                    expanded[key] = context["params"].get(ref)
            else:
                expanded[key] = value
        return expanded

    # ==================== 查询 ====================

    def get_runs(self, workflow_name: Optional[str] = None,
                 limit: int = 20) -> List[Dict[str, Any]]:
        """查询执行历史"""
        runs = self._runs
        if workflow_name:
            runs = [r for r in runs if r["workflow"] == workflow_name]
        return list(reversed(runs))[:limit]
