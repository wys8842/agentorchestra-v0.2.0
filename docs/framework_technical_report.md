# Symphony 框架完整技术报告

> **版本**: 0.2.0  
> **报告范围**: 全框架模块、功能点、知识点的深度解析  
> **目标读者**: 框架使用者、贡献者、技术决策者

---

## 目录

1. [框架概览](#1-框架概览)
2. [架构设计](#2-架构设计)
3. [Runtime 运行时域](#3-runtime-运行时域)
4. [Capability 能力域](#4-capability-能力域)
5. [Orchestration 编排域](#5-orchestration-编排域)
6. [Governance 治理域](#6-governance-治理域)
7. [Ontology 企业级本体](#7-ontology-企业级本体)
8. [Observability 可观测性](#8-observability-可观测性)
9. [Components 统一装配](#9-components-统一装配)
10. [核心数据流与交互](#10-核心数据流与交互)
11. [改进建议与未来规划](#11-改进建议与未来规划)

---

## 1. 框架概览

### 1.1 定位

Symphony 是一个面向生产的**企业级多智能体编排框架**，核心设计哲学是：

- **职责分离**: Agent 负责思考与决策，Ontology 承载业务语义，框架负责编排/事务/持久化/治理
- **可插拔**: 横切组件（存储、追踪、指标）通过统一门面装配
- **可恢复**: 内置 WAL + Checkpoint + 幂等 + 补偿，保证崩溃恢复与事务一致性
- **企业级**: 多租户隔离、配额管理、WORM 审计、行级 ACL

### 1.2 五层架构

```
┌──────────────────────────────────────────────┐
│  应用层 (Application Layer)                  │
│    用户代码 / 业务场景 / Agent 编排           │
├──────────────────────────────────────────────┤
│  Agent 层 (agents + core)                    │
│    Simple / ReAct / Reflection / PlanSolve / │
│    Loop(含 Plan→Act→Observe→Reflect→Check→   │
│    Replan 认知闭环)                          │
├──────────────────────────────────────────────┤
│  Tool 契约层 (tools + context)               │
│    ToolRegistry / Truncator / TokenCounter / │
│    CircuitBreaker                            │
├──────────────────────────────────────────────┤
│  业务语义层 (ontology)                        │
│    ObjectType / LinkType / ActionType /      │
│    Function / Interface / Workflow /         │
│    Transaction                               │
├──────────────────────────────────────────────┤
│  数据层 (state + governance)                 │
│    CheckpointStore / WAL / Lock / Inbox /    │
│    Audit / Tenant                            │
└──────────────────────────────────────────────┘
```

### 1.3 代码组织

```
agentorchestra/
├── runtime/             # 运行时域
│   ├── agents/          # Agent 范式
│   ├── core/            # 核心运行时（LLM/Config/Message/）
│   ├── context/         # 上下文工程
│   └── capabilities/    # 能力组件（TraceLogger/MemoryManager 等）
├── capability/          # 能力域
│   ├── tools/           # 工具系统
│   ├── skills/          # Skills 知识外化
│   └── memory/          # 跨会话记忆
├── ontology/            # 企业级本体
│   ├── semantic/        # 语义层（对象/链接/接口）
│   ├── kinetic/         # 动能层（动作/函数）
│   ├── storage/         # 存储后端
│   ├── process/         # 流程（Workflow/Scheduler/Transaction）
│   └── governance/      # 本体治理
├── orchestration/       # 编排域
│   ├── orch/            # Graph/Scheduler/Inbox/Node
│   └── state/           # Checkpoint/WAL/Interrupt/Snapshot
├── governance/          # 治理域
│   ├── govern/          # Identity/ACL/Permission/CAS
│   ├── tx/              # 事务运行时
│   └── tenancy/         # 多租户
├── observability/       # 可观测性
│   ├── trace_logger.py  # 追踪日志
│   ├── prometheus.py    # Prometheus 指标
│   └── otel_exporter.py # OTLP trace 导出
└── components.py        # 统一装配门面
```

### 1.4 模块总数

| 域 | 子模块 | 文件数 | 核心类 |
|------|--------|--------|--------|
| Runtime | 4 | ~30 | 30+ |
| Capability | 3 | ~20 | 25+ |
| Orchestration | 2 | ~15 | 20+ |
| Governance | 3 | ~15 | 15+ |
| Ontology | 6 | ~25 | 30+ |
| Observability | 4 | ~6 | 10+ |
| Components | 1 | 1 | 2 |

**总计**: ~120 个 Python 文件，150+ 个核心类/接口。

---

## 2. 架构设计

### 2.1 设计原则

#### 2.1.1 领域驱动设计（DDD）

源码按**业务领域**组织而非技术层次：
- `runtime/core` 是基础设施
- `runtime/agents` 是 Agent 范式
- `capability/tools` 是工具能力
- `ontology` 是业务语义

#### 2.1.2 opt-in by default

所有非核心 feature 默认关闭，避免隐式副作用：

```python
class Config(BaseModel):
    """Symphony 顶层配置。所有 feature flag 默认 False（opt-in by default）"""
    llm: LLMConfig = LLMConfig()               # 核心，默认开启
    system: SystemConfig = SystemConfig()      # 核心
    
    # opt-in features
    skills: SkillsConfig = SkillsConfig()      # 默认 enabled=False
    mcp: MCPConfig = MCPConfig()               # 默认 enabled=False
    memory: MemoryConfig = MemoryConfig()      # 默认 enabled=False
    subagent: SubAgentConfig = SubAgentConfig()
    todowrite: TodoWriteConfig = TodoWriteConfig()
    devlog: DevLogConfig = DevLogConfig()
    session: SessionConfig = SessionConfig()
    ontology: OntologyConfig = OntologyConfig()
```

**为什么这样设计**: 用户只想用基本功能时，框架不会偷偷启动 MCP 客户端、扫描 skills 目录、建 SQLite 表。

#### 2.1.3 渐进增强

新特性默认关闭，向后兼容：

```python
class LoopAgent(Agent):
    """闭环认知 Agent - 向后兼容"""
    def __init__(self, ..., enable_reflection=False, enable_replan=False):
        # 默认行为与 v0.1.x 完全一致
        # 启用新特性后才进入 Plan→Act→Observe→Reflect→Check→Replan
```

#### 2.1.4 细粒度接口

CheckpointStore 拆分为 10 个细粒度接口（见 [interfaces.py]）：

```python
class ThreadStore(ABC):       # 线程管理
class CheckpointStore(ABC):   # 检查点 CRUD
class WALStore(ABC):          # Write-Ahead Log
class SnapshotStore(ABC):     # 快照
class InterruptStore(ABC):    # 中断管理
class LockStore(ABC):         # 分布式锁
class IdempotencyStore(ABC):  # 幂等记录
class DLQStore(ABC):          # 死信队列
class InboxStore(ABC):        # 消息 inbox
class AuditStore(ABC):        # 审计日志
```

**为什么拆分**: 单一 God Interface（27 个 abstractmethod）难以：
- 单独 mock（测试时只需要 InboxStore）
- 单独替换（如自定义 AuditStore 不影响 CheckpointStore）
- 遵循 ISP（接口隔离原则）

#### 2.1.5 向后兼容

经典扁平导入路径自动映射到领域化物理路径：

```python
# agentorchestra/_legacy.py
def install_legacy_aliases():
    sys.modules['agentorchestra.core.llm'] = sys.modules['agentorchestra.runtime.core.llm']
    sys.modules['agentorchestra.tools.registry'] = sys.modules['agentorchestra.capability.tools.registry']
    # ...
```

用户无需修改旧代码。

### 2.2 控制流

#### 2.2.1 LoopAgent 认知闭环

```
                    ┌──────────┐
                    │  PROMPT  │  _build_messages
                    └────┬─────┘
                         ↓
                    ┌──────────┐
              ┌────│   PLAN   │◄──────────────┐
              │    └────┬─────┘               │
              │         ↓                     │
              │    ┌──────────┐               │
              │    │   ACT    │               │  replan
              │    └────┬─────┘               │
              │         ↓                     │
              │    ┌──────────┐               │
              │    │ OBSERVE  │               │
              │    └────┬─────┘               │
              │         ↓                     │
              │    ┌──────────┐               │
              └───▶│ REFLECT  │               │
                   └────┬─────┘               │
                        ↓                     │
                   ┌──────────┐               │
                   │  CHECK   │── stop ──▶ OUTPUT
                   └────┬─────┘               │
                        │ continue/replan     │
                        └─────────────────────┘
```

#### 2.2.2 GraphScheduler 调度

```
主循环:
  1. inbox.poll(thread_id)              # 获取待处理消息
  2. _process_one(msg)                  # 单消息处理
     - 节点执行
     - 路由下游
  3. inbox.ack(msg_id)                  # 确认
  4. 重复直到无消息
```

---

## 3. Runtime 运行时域

### 3.1 LLM 客户端（SymphonyLLM）

#### 3.1.1 功能点

- 统一接口访问 OpenAI / Anthropic / Gemini / DeepSeek / 自定义 endpoint
- 自动 base_url 识别 Provider
- 流式输出（sync / async）
- Function Calling（tools）
- 重试机制
- Token 统计

#### 3.1.2 实现

```python
class SymphonyLLM:
    def __init__(self, model, api_key=None, base_url=None, provider=None):
        # 根据 base_url 或 provider 推断后端
        if provider == "openai" or "openai" in (base_url or ""):
            self.adapter = OpenAIAdapter(model, api_key, base_url)
        elif provider == "anthropic" or "anthropic" in (base_url or ""):
            self.adapter = AnthropicAdapter(model, api_key, base_url)
        # ...
```

#### 3.1.3 代码解析

`SymphonyLLM.invoke_with_tools()` 调用流程：
1. 序列化 messages 列表
2. 调用对应 adapter 的 chat.completions.create()
3. 解析 tool_calls（如果 LLM 决定调用工具）
4. 返回统一格式响应

#### 3.1.4 改进建议

- **统一工具 schema**: 当前不同 Provider 的 tools 参数格式略有差异
- **更细粒度的错误分类**: 当前捕获 Exception 太宽
- **缓存 LLM 响应**: 对于确定性场景可加 response cache

### 3.2 Config 配置管理

#### 3.2.1 功能点

- 子配置分组（LLMConfig / SystemConfig / HistoryConfig / ...）
- 配置继承（Config.development() / Config.production()）
- 环境变量加载（Config.from_env()）
- JSON 文件加载（Config.from_file()）
- 配置脱敏（sanitized_dict）

#### 3.2.2 实现

```python
class Config(BaseModel):
    llm: LLMConfig = LLMConfig()
    system: SystemConfig = SystemConfig()
    history: HistoryConfig = HistoryConfig()
    smart_compression: SmartCompressionConfig = SmartCompressionConfig()
    # ...
    
    def __getattr__(self, name):
        # 向后兼容：旧字段名映射到子配置
        if name in _LEGACY_FIELD_MAP:
            sub_name, sub_field = _LEGACY_FIELD_MAP[name]
            return getattr(getattr(self, sub_name), sub_field)
        # ...
```

**为什么这样设计**: 让 `config.temperature == config.llm.temperature`，旧代码无需修改。

#### 3.2.3 改进建议

- **配置验证**: 应在启动时检查配置冲突（如 max_tokens > context_window）
- **配置热更新**: 已有 hot_config.py，但未完全接入

### 3.3 Agent 范式

#### 3.3.1 SimpleAgent

最简单 Agent，直接 LLM 调用：

```python
def run(self, input_text):
    response = self.llm.invoke(self._build_messages(input_text))
    return response.content
```

**适用**: 基础对话、无工具场景

#### 3.3.2 ReActAgent

ReAct 范式：Reason + Act 循环

```python
while current_step < self.max_steps:
    response = self.llm.invoke_with_tools(messages, tools)
    tool_calls = response.tool_calls
    
    if not tool_calls:
        return response.content  # 无工具调用 → 终止
    
    for tc in tool_calls:
        result = self._execute_tool(tc)
        messages.append({"role": "tool", "content": result})
```

**适用**: 需要多次工具调用的任务

#### 3.3.3 ReflectionAgent

反思迭代：先答，再反思，再改进

```python
for iteration in range(max_iterations):
    response = self.llm.invoke(messages)
    reflection = self.llm.invoke(reflection_prompt)
    
    if "无需改进" in reflection:
        return response
    
    messages.append({"role": "user", "content": reflection})
```

**适用**: 对答案质量要求高的场景

#### 3.3.4 PlanSolveAgent

计划-执行分离：

```python
plan = self.llm.invoke(plan_prompt)
for step in plan.steps:
    result = self.execute_step(step)
```

**适用**: 多步骤复杂任务

#### 3.3.5 LoopAgent

**最复杂的 Agent**，实现完整认知闭环：

```python
class LoopAgent(Agent):
    """Plan → Act → Observe → Reflect → Check → Replan"""
    
    def run(self, input_text):
        state = LoopState(goal=input_text)
        messages = self._build_messages(input_text)
        
        while state.budget.current_steps < state.budget.max_steps:
            # PLAN
            response = self._plan(state, messages)
            
            # CHECK (before act)
            decision = self._check_done(state, bool(response.tool_calls))
            if decision.action == "stop":
                return response.content
            
            # ACT
            tool_results = self._run_tools_sync(response.tool_calls)
            
            # OBSERVE
            self._observe(tool_results, state)
            
            # REFLECT
            if self.enable_reflection:
                reflection = self._reflect(state)
            
            # CHECK (after observe)
            decision = self._check_done(state)
            if decision.action == "replan" and state.budget.current_replans < state.budget.max_replans:
                state.plan = self._replan(state)
```

**核心数据类**:
- `Plan`: 结构化计划
- `Evidence`: 工具执行证据
- `Reflection`: 反思结果（progress / issues / next_strategy）
- `Budget`: 预算控制（max_steps / max_replans）
- `TerminationDecision`: 终止决策（signal / action / reason）

**多信号终止**:
| 信号 | 触发 | 动作 |
|------|------|------|
| terminate_tool | 模型显式调用 terminate | stop |
| completed | _is_goal_met() | stop |
| budget | current_steps >= max_steps | stop |
| errors | 连续错误 >= max_consecutive_errors | stop |
| stuck | 连续重复调用 | replan |
| no_progress | 无 tool_calls 且有 evidence | stop |

**适用**: 复杂多步任务、显式认知控制场景

#### 3.3.6 改进建议

- **Sub-Agent 集成**: 当前 TaskTool 子代理机制，但未与 LoopAgent 深度集成
- **预算可视化**: 没有 UI 显示当前 budget 状态
- **Check 自定义**: 应允许用户自定义 goal checker

### 3.4 上下文工程

#### 3.4.1 HistoryManager

```python
class HistoryManager:
    """历史管理：压缩 / 边界检测 / 序列化"""
    
    def __init__(self, min_retain_rounds=10, compression_threshold=0.8):
        # min_retain_rounds: 压缩时保留的最近轮数
        # compression_threshold: 触发压缩的阈值
```

**改进建议**: 应支持分层记忆（短期/工作/长期）

#### 3.4.2 TokenCounter

```python
class TokenCounter:
    """Token 计数（基于 tiktoken）"""
    
    def count_messages(self, messages) -> int:
        # 精确计算每条消息的 token
```

**改进建议**: 应支持多模型 tokenizer 自动选择

#### 3.4.3 ObservationTruncator

```python
class ObservationTruncator:
    """工具输出截断"""
    
    def __init__(self, max_lines=2000, max_bytes=51200, truncate_direction="head"):
        # max_lines: 最大行数
        # max_bytes: 最大字节数
        # truncate_direction: "head" 保留开头 / "tail" 保留结尾
```

#### 3.4.4 ContextBuilder

GSSC（Get-Select-Structure-Compress）上下文构建器：

```python
class ContextBuilder:
    """GSSC 上下文构建器"""
    
    def build(self, user_input, history, system_prompt):
        # Get: 获取所有可能相关的内容
        # Select: 按相关性筛选
        # Structure: 结构化组织
        # Compress: 压缩到 token 预算内
```

**改进建议**: 当前仅基础实现，未做语义级相关性排序

### 3.5 Capabilities 模块

`runtime/capabilities/` 包含内置能力组件（TraceLogger/MemoryManager/SkillLoader/MCP 等），通过 Capability 接口注册到 Agent。

---

## 4. Capability 能力域

### 4.1 工具系统

#### 4.1.1 Tool 基类

```python
class Tool(ABC):
    name: str
    description: str
    
    @abstractmethod
    def get_parameters(self) -> List[ToolParameter]:
        ...
    
    @abstractmethod
    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        ...
```

**ToolParameter**:
```python
@dataclass
class ToolParameter:
    name: str
    type: str  # string / number / boolean / object
    description: str
    required: bool = False
    default: Any = None
    enum: Optional[List[str]] = None
```

**为什么这样设计**: ToolParameter 支持 Pydantic-like 校验，自动生成 JSON Schema 喂给 LLM。

#### 4.1.2 ToolRegistry

```python
class ToolRegistry:
    def register_tool(self, tool: Tool):
        # 展开 expandable 工具（自动拆分为多个子工具）
        ...
    
    def execute_tool(self, name: str, args: Dict) -> ToolResponse:
        # 同步执行（带熔断检查）
        ...
    
    async def async defct_tool(self, name: str, args: Dict) -> ToolResponse:
        # 异步执行（gather + Semaphore 并发）
        ...
```

**Tool 过滤**（contextvars RAII）：
```python
_disabled_tools_var = ContextVar("disabled_tools", default=set())

def disable_tools(*names):
    tokens = []
    for name in names:
        current = list(_disabled_tools_var.get())
        current.append(name)
        tokens.append(_disabled_tools_var.set(current))
    return tokens

def restore_tools(tokens):
    for token in tokens:
        _disabled_tools_var.reset(token)
```

**为什么用 contextvars**: 子代理隔离，避免全局状态污染。RAII 风格确保异常路径也能恢复。

#### 4.1.3 CircuitBreaker

```python
class CircuitBreaker:
    """熔断器：防止级联失败"""
    
    def __init__(self, failure_threshold=3, recovery_timeout=300, enabled=True):
        # failure_threshold: 触发熔断的连续失败次数
        # recovery_timeout: 熔断恢复时间（秒）
```

**状态机**: CLOSED → OPEN（达到阈值） → HALF_OPEN（超时后） → CLOSED（成功）或 OPEN（失败）

**改进建议**: 应支持半开探测、自定义熔断策略

#### 4.1.4 内置工具

| 工具 | 功能 |
|------|------|
| CalculatorTool | 数学计算 |
| FileTools (Read/Write/ListDir) | 文件操作 |
| MCPTool | MCP 协议集成 |
| TaskTool | 子代理调用 |
| SkillTool | Skills 调用 |
| TodoWriteTool | 待办事项 |
| DevLogTool | 开发日志 |

**MCP 集成**（重要）：
```python
class MCPTool(Tool):
    """MCP (Model Context Protocol) 集成"""
    
    def __init__(self, server_url: str):
        # 通过 MCP 协议连接外部服务
        ...
```

### 4.2 Memory 跨会话记忆

#### 4.2.1 MemoryManager

```python
class MemoryManager:
    def __init__(self, store, embedder, keyword_index, retriever, ...):
        # store: MemoryStore (SQLite/JSONL)
        # embedder: Embedder (向量嵌入)
        # keyword_index: KeywordIndex (关键词索引)
        # retriever: HybridRetriever (混合检索)
    
    def remember(self, content, record_type, namespace):
        # 存储记忆（带去重）
        ...
    
    def recall(self, query, top_k=5, namespace):
        # 混合检索：关键词 + 向量
        ...
    
    def summarize(self, namespace):
        # 摘要（基于 LLM）
        ...
```

**架构**：
```
┌─────────────────────────────┐
│      MemoryManager          │
├─────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  │
│  │ Store   │  │ Embedder│  │
│  │(SQLite/ │  │(向量嵌入)│  │
│  │ JSONL)  │  │         │  │
│  └─────────┘  └─────────┘  │
│  ┌─────────┐  ┌─────────┐  │
│  │Keyword  │  │Hybrid   │  │
│  │ Index   │  │Retriever│  │
│  └─────────┘  └─────────┘  │
└─────────────────────────────┘
```

**特性**：
- 去重（基于相似度，threshold=0.92）
- 衰减（time decay, configurable tau）
- 命名空间隔离
- 摘要压缩

**改进建议**:
- **记忆分级**: 短期/工作/长期三级缓存
- **遗忘机制**: LRU 驱逐
- **图记忆**: 实体关系图

#### 4.2.2 Embedder

```python
class Embedder:
    def __init__(self, llm=None, enabled=True, cache_size=10000):
        # 支持任意 LLM 作为嵌入源
        # 缓存避免重复计算
```

### 4.3 Skills 知识外化

#### 4.3.1 SkillLoader

```python
class SkillLoader:
    def __init__(self, skills_dir: Path):
        # 扫描 skills_dir 发现 Skill
        ...
```

**Skill 结构**:
```
skills/
├── my-skill/
│   ├── metadata.yaml    # 渐进披露：默认加载
│   └── body.md          # 按需加载
```

**渐进披露原理**: LLM 默认只能看到 metadata（轻量），需要时才请求 body（重型）。节省 Token。

**改进建议**:
- **Skill 版本控制**: 当前未跟踪版本
- **Skill 依赖**: 未支持 Skill 间依赖
- **Skill 测试**: 应提供 Skill 单元测试框架

---

## 5. Orchestration 编排域

### 5.1 Graph/Node/Inbox

#### 5.1.1 Graph

```python
class Graph:
    def __init__(self):
        self._nodes: Dict[str, Node] = {}
        self._edges: List[Tuple[str, str, Optional[Callable]]] = []
    
    def add_node(self, name: str, node: Node):
        ...
    
    def add_edge(self, source: str, target: str, when: Optional[Callable] = None):
        # when: 条件路由函数
        ...
    
    def validate(self) -> List[str]:
        # 验证：是否有入口、无环、节点存在
        ...
```

**改进建议**: 应支持子图、嵌套图、模板图

#### 5.1.2 Node 类型

```python
class Node(ABC):
    @abstractmethod
    async def run(self, message: Dict, ctx: NodeContext) -> NodeOutput:
        ...
```

**AgentNode / RouterNode / MergeNode / FunctionalNode**

#### 5.1.3 Inbox（消息通信）

```python
class Inbox:
    """节点间消息传递（落库保证可追溯）"""
    
    async def send(self, graph_id, thread_id, to_node, content, from_node=None, condition=None):
        # 消息入队
        ...
    
    async def poll(self, thread_id, to_node=None, limit=100) -> List[InboxMessage]:
        # 拉取待处理消息
        ...
    
    async def mark_delivered(self, msg_id) -> str:
        # 标记已投递（返回 ack_token）
        ...
    
    async def ack(self, msg_id, ack_token, status="acked"):
        # 写回执
        ...
```

**为什么落库**: 
1. **可追溯**: 7 天内可回放消息
2. **投递回执**: 确认/失败/重试
3. **崩溃恢复**: 重启后可继续消费未确认消息

### 5.2 GraphScheduler

#### 5.2.1 实现

```python
class GraphScheduler:
    def __init__(self, store, max_iterations=3, message_ttl_seconds=600):
        self.store = store
        self.max_iterations = max_iterations
    
    async def execute(self, graph, initial_message, thread_id, ...):
        # 1. 入口节点消息入队
        # 2. 主循环：poll → process → ack
        # 3. 处理：节点执行 + 路由下游
        ...
```

#### 5.2.2 Fan-in Barrier（多上游汇聚）

```python
# 等待所有上游到达才激活
fanin_pending: Dict[str, set] = {}  # target -> {from_node}
fanin_expected: Dict[str, int] = {}  # target -> expected count

for edge in graph.outgoing(node_name):
    if fanin_expected[edge.target] > len(fanin_pending.get(edge.target, set())):
        continue  # 还有上游未到，跳过
```

**改进建议**: 当前实现较简单，应支持更复杂的 barrier（带超时）

### 5.3 State 状态管理

#### 5.3.1 CheckpointStore（细粒度接口）

**为什么拆分 10 个接口**:
- 单一职责（ISP）
- 可单独 mock（测试）
- 可单独替换（如自定义 AuditStore）

#### 5.3.2 InMemoryCheckpointStore

```python
class InMemoryCheckpointStore:
    """内存后端（用于测试/开发）"""
    
    async def save_checkpoint(self, cp: Checkpoint):
        self._checkpoints[(cp.thread_id, cp.checkpoint_id)] = cp
    
    async def load_checkpoint(self, thread_id, checkpoint_id):
        return self._checkpoints.get((thread_id, checkpoint_id))
```

**特性**:
- 完整 30+ 方法实现
- 支持所有细粒度接口
- Thread-safe（asyncio.Lock）

#### 5.3.3 SQLiteCheckpointStore / PostgresCheckpointStore

基于 SQLAlchemy 2.0 async 实现。

**SQLite 多进程安全序列**:
```python
# 解决 _fencing_seq 多进程不安全的问题
# 方案：使用数据库表模拟 sequence
class _SequenceRow(Base):
    seq_name: str  # 主键
    seq_value: int  # 原子递增
```

**CAS 操作**（乐观锁）:
```python
async def compare_and_swap(self, resource_key, expected_version, owner_tx):
    # 原子操作：UPDATE ... WHERE version = expected_version
    ...
```

#### 5.3.4 WAL（Write-Ahead Log）

```python
class WALEntry:
    thread_id: str
    action_type: str
    payload: Dict
    tx_id: Optional[str]
    created_at: datetime
```

**WAL 写入流程**:
1. 写入 WAL（持久化）
2. 执行操作
3. 写入 checkpoint

**崩溃恢复**: 从最近 checkpoint + WAL 重放

#### 5.3.5 分布式锁（LockStore）

```python
class LockStore(ABC):
    @abstractmethod
    async def acquire_lock(self, resource_key, owner_tx, ttl_seconds=30.0):
        ...
    
    @abstractmethod
    async def compare_and_swap(self, resource_key, expected_version, owner_tx, expected_fencing_token=None):
        ...
```

**Fencing Token（防僵尸事务）**:
- 单调递增的 token
- 每次 acquire 锁时 +1
- 下游 CAS 操作需匹配 expected_fencing_token

**SQLite sequence 实现**:
```python
async def acquire_lock(self, resource_key, owner_tx, ttl_seconds):
    if dialect == "postgresql":
        seq_result = await conn.execute(select(func.nextval(seq_name)))
    elif dialect == "sqlite":
        # 原子更新序列
        await conn.execute(
            sqlite_insert(_SequenceRow)
            .values(seq_name="fencing_token", seq_value=1)
            .on_conflict_do_update(set_={"seq_value": _SequenceRow.seq_value + 1})
        )
    else:
        warnings.warn("in-process counter unsafe for multi-process")
        ...
```

---

## 6. Governance 治理域

### 6.1 Identity 身份

```python
class IdentityService:
    def get_current_principal() -> str:
        # 当前 principal（用户/服务/agent）
        ...
```

### 6.2 ACL 行级访问控制

```python
class ACLManager:
    def grant(self, principal, resource, action):
        ...
    
    def check(self, principal, resource, action) -> bool:
        # 行级 ACL
        ...
```

### 6.3 Permission 权限

```python
class PermissionChecker:
    def check(self, action, resource) -> bool:
        # RBAC 权限检查
        ...
```

### 6.4 CAS 对象级乐观锁

```python
class ObjectCAS:
    """对象级 Compare-And-Swap"""
    
    async def compare_and_swap(self, obj_id, expected_version, new_data) -> bool:
        ...
```

### 6.5 WORM 审计

WORM = Write Once Read Many。审计日志只能追加，不能修改：

```python
class AuditStore(ABC):
    @abstractmethod
    async def append_audit(self, entry: AuditEntry):
        ...
    
    # 没有 update_audit / delete_audit 方法
```

**改进建议**: 应支持合规导出（GDPR 等）

### 6.6 多租户（Tenancy）

#### 6.6.1 TenantContext

```python
class TenantContext:
    def __init__(self, tenant_id: str, user_id: str = ""):
        ...
    
    def __enter__(self):
        # 进入租户上下文
        ...
```

**ContextVar 隔离**:
```python
_current_tenant_var: ContextVar[TenantContext] = ContextVar("tenant")
```

#### 6.6.2 namespace_resource

```python
def namespace_resource(resource_key: str) -> str:
    """自动添加租户前缀"""
    tenant = current_tenant_var.get(None)
    if tenant:
        return f"{tenant.tenant_id}:{resource_key}"
    return resource_key
```

**为什么不传 tenant 参数**: 上下文自动注入，调用方无需关心

#### 6.6.3 QuotaManager

```python
class QuotaManager:
    def set_limit(self, tenant_id: str, limit: int):
        ...
    
    def record_usage(self, tenant_id: str, amount: int):
        ...
    
    def check_quota(self, tenant_id: str, amount: int) -> bool:
        # 返回是否超额
        ...
```

### 6.7 TX 事务运行时

#### 6.7.1 OptimisticLock

```python
class OptimisticLock:
    """乐观锁：version + fencing_token"""
    
    async def acquire(self, resource, owner_tx, ttl):
        ...
    
    async def compare_and_swap(self, resource, expected_version, owner_tx, expected_fencing_token=None):
        ...
```

**为什么用乐观锁**：避免长事务阻塞；适合读多写少场景。

#### 6.7.2 Saga 补偿

```python
class CompensableAction:
    forward_fn: Callable  # 正向
    compensate_fn: Callable  # 补偿
    
async def execute_with_compensation(actions):
    """执行链，失败时逆序补偿"""
    executed = []
    for action in actions:
        try:
            await action.forward()
            executed.append(action)
        except:
            # 逆序补偿
            for a in reversed(executed):
                await a.compensate()
            raise
```

---

## 7. Ontology 企业级本体

### 7.1 语义层

#### 7.1.1 ObjectType

```python
class ObjectType:
    name: str
    primary_key: str
    properties: List[ToolParameter]
    link_types: List[LinkType]
```

#### 7.1.2 LinkType

```python
class LinkType:
    name: str
    source: str  # 源对象类型
    target: str  # 目标对象类型
```

#### 7.1.3 Interface

```python
class Interface:
    name: str
    required_properties: List[str]
    required_actions: List[str]
```

### 7.2 动能层

#### 7.2.1 ActionType

```python
class ActionType:
    name: str
    parameters: List[ToolParameter]
    rules: List[Callable]  # 校验规则
    execute_fn: Callable  # 执行函数
```

**执行流程**:
1. 参数校验
2. 规则检查（如 check_amount）
3. 权限检查（SecurityContext）
4. 执行 execute_fn
5. 写入 ObjectStore

#### 7.2.2 Function

```python
class Function:
    name: str
    impl: Callable
    arguments: List[ToolParameter]
```

### 7.3 存储层

#### 7.3.1 ObjectStore

```python
class ObjectStore:
    def __init__(self, graph: GraphStore):
        # 基于 GraphStore 存储对象
        ...
    
    def insert(self, obj_type, data) -> str:
        # 插入对象，返回 obj_id
        ...
    
    def update(self, obj_id, new_data) -> bool:
        # 乐观锁更新（version+1）
        ...
```

#### 7.3.2 GraphStore

```python
class GraphStore:
    """图存储（节点+边）"""
    
    def create_node(self, type, data) -> str:
        ...
    
    def create_edge(self, source, edge_type, target):
        ...
```

### 7.4 流程层

#### 7.4.1 Workflow

```python
class Workflow:
    def add_node(self, step: StepNode, entry=False):
        ...
    
    def run(self, name, ctx) -> Dict:
        # 拓扑排序 → 顺序执行
        ...
```

#### 7.4.2 Transaction

```python
class Transaction:
    def register(self, name, forward_fn, compensate_fn):
        ...
    
    async def execute(self, actions) -> Dict:
        # 正向执行 + 失败时逆序补偿
        ...
```

#### 7.4.3 Scheduler

```python
class Scheduler:
    def add_interval(self, name, fn, interval_seconds, max_runs=None):
        # 定时任务
        ...
```

### 7.5 本体治理

#### 7.5.1 SecurityContext

```python
class SecurityContext:
    def __init__(self, principal: str, roles: List[str]):
        ...
    
    def check(self, resource, action) -> bool:
        # RBAC + ABAC 检查
        ...
```

#### 7.5.2 Branching（分支）

```python
class Branching:
    def snapshot(self, name):
        # 创建分支快照
        ...
    
    def switch(self, name):
        # 切换分支（时间旅行）
        ...
```

#### 7.5.3 QueryEngine

```python
class QueryEngine:
    def object_set(self, obj_type, conditions=None, limit=50) -> Dict:
        ...
    
    def navigate(self, obj_id, edge_type, depth=1) -> List:
        # 图遍历
        ...
```

### 7.6 Ontology 与 Agent 集成

```python
# 将 Ontology 暴露为 Agent 可用工具
mounted = engine.mount(registry)
# 自动注册:
#   - Query<Type>: 查询对象
#   - create_<ActionType>: 执行动作
#   - Call<Function>: 调用函数
```

**改进建议**: 应支持更细粒度的权限继承

---

## 8. Observability 可观测性

### 8.1 TraceLogger

```python
class TraceLogger:
    """双格式追踪：JSONL + HTML"""
    
    def __init__(self, output_dir="memory/traces", sanitize=True):
        # sanitize: 自动脱敏敏感字段
        ...
    
    def log_event(self, event_type: str, data: Dict, step: int = 0):
        # 记录事件（带 event_id 自增）
        ...
    
    def trace(self, name: str):
        # 上下文管理器（with 块）
        ...
    
    def finalize(self):
        # 输出 JSONL + HTML 文件
        ...
```

**特性**:
- 单调递增 event_id（避免 bounded 弹出后冲突）
- JSONL 单文件大小限制 + rotate
- 敏感字段自动脱敏

### 8.2 MetricsCollector

```python
class MetricsCollector(ABC):
    @abstractmethod
    def increment(self, name: str, value: int = 1):
        ...
    
    @abstractmethod
    def gauge(self, name: str, value: float):
        ...
    
    @abstractmethod
    def observe(self, name: str, value: float):
        ...
```

**实现**:
- `NoOpCollector`: 默认（零开销）
- `PrometheusTextCollector`: Prometheus 文本格式

### 8.3 OTLP 导出

```python
class OTLPHttpJsonExporter:
    """OTLP HTTP/JSON trace 导出"""
    
    def __init__(self, endpoint="http://localhost:4318", service_name="agentorchestra"):
        ...
    
    def enable(self):
        # 接入全局 Tracer
        ...
```

**特性**:
- SpanBatcher（size 阈值 / window 时间）
- 单次 POST 多 span（resourceSpans[].scopeSpans[].spans[]）
- 避免高频小 POST

---

## 9. Components 统一装配

### 9.1 设计动机

**问题**: 业务包散落全局单例（state_store / tracer / metrics_collector 各自维护）
**方案**: 统一入口 + 懒加载 + 可插拔

### 9.2 实现

```python
class Components:
    """装配注册表（模块级单例）"""
    
    def register_state_store(self, factory):
        # 替换默认实现
        ...
    
    def state_store(self):
        # 显式注册优先；否则回退默认
        ...
```

### 9.3 常用组合

```python
# 启用 Prometheus
Components.enable_prometheus()

# 启用 OTLP trace
Components.enable_otel_trace(endpoint="...")

# 自定义存储
Components.register_state_store(my_store)
```

---

## 10. 核心数据流与交互

### 10.1 Agent 执行流程

```
用户输入
    ↓
build_messages (system + history + GSSC)
    ↓
llm.invoke_with_tools (messages, tools)
    ↓
[tool_calls?] -- no --> return content
    ↓ yes
[parallel execute tools] (gather + Semaphore)
    ↓
truncate tool results (Truncator)
    ↓
append tool messages to history
    ↓
loop until no tool_calls or max_steps
    ↓
return final content
```

### 10.2 图编排执行流程

```
initial_message → inbox.send → graph scheduler
                                     ↓
                              inbox.poll (thread_id)
                                     ↓
                              for msg in messages:
                                  - execute node
                                  - route downstream
                                  - inbox.ack
                                     ↓
                              repeat until no messages
                                     ↓
                              return GraphResult
```

### 10.3 事务执行流程

```
begin transaction
    ↓
execute action 1
    ↓
execute action 2
    ↓
[failed?] -- yes --> inverse order compensation
    ↓ no
commit
```

### 10.4 崩溃恢复流程

```
启动时: load_latest_snapshot
    ↓
load_wal (after snapshot.up_to_seq)
    ↓
replay each WAL entry
    ↓
current state restored
```

---

## 11. 改进建议与未来规划

### 11.1 高优先级改进

#### 11.1.1 错误处理收窄

**问题**: 165 个 `except Exception` 太宽

**方案**:
```python
# 当前（太宽）
except Exception as e:
    logger.warning(...)

# 改进（精确）
except (TypeError, ValueError) as e:
    logger.warning(...)
except RuntimeError as e:
    logger.error(...)
# 其他异常向上传播
```

#### 11.1.2 type: ignore 消除

**问题**: 65 个 `# type: ignore`

**方案**:
- 使用 Protocol 定义接口
- 添加 TypeGuard 替代 isinstance
- 用 NewType 增强类型信息

#### 11.1.3 多进程 sequence 完善

**问题**: `_fencing_seq` 多进程不安全（已部分修复）

**方案**: 已用 SQLite 表模拟；生产建议 PG sequence

### 11.2 性能优化

#### 11.2.1 并行工具执行

**当前**: `asyncio.gather + Semaphore(max_concurrent_tools=3)`

**改进**:
- 动态调整并发度（基于系统负载）
- 工具依赖分析（独立工具才能并行）

#### 11.2.2 LLM 响应缓存

**适用场景**: 确定性任务

```python
@cached(ttl=3600, key=lambda messages: hash(messages))
def invoke_cached(messages, ...):
    return llm.invoke(messages)
```

#### 11.2.3 Token 计算增量

**当前**: 每次重新计算所有消息

**改进**: 增量计算（只计算新增消息）

### 11.3 可观测性增强

#### 11.3.1 OpenTelemetry 完整集成

**当前**: 基础 OTLP 导出

**改进**: 完整 OTEL SDK（Trace/Metric/Log 三件套）

#### 11.3.2 分布式追踪

**当前**: 单进程追踪

**改进**: Trace Context（W3C TraceContext）跨服务传递

### 11.4 安全加固

#### 11.4.1 沙箱执行

**当前**: 工具直接调用 Python 函数

**改进**: 
- 进程隔离（subprocess）
- WASM 沙箱（如 Wasmtime）
- 网络隔离（白名单）

#### 11.4.2 Prompt 注入防护

**当前**: 无防护

**改进**:
- 用户输入清洗
- 工具输出标记（避免被 LLM 误用）
- 危险操作二次确认

### 11.5 生态完善

#### 11.5.1 更多 Agent 范式

- Tree-of-Thoughts
- ReWOO（Reasoning without Observation）
- AutoGPT 风格自主 Agent

#### 11.5.2 工具市场

- Skill 共享平台
- Tool 注册中心
- 社区模板

#### 11.5.3 可视化

- Graph 可视化编辑器
- 实时执行追踪
- Performance Dashboard

### 11.6 文档与测试

#### 11.6.1 API 文档

- 自动生成（从 docstring）
- OpenAPI 规范
- 示例代码库

#### 11.6.2 集成测试

- E2E 场景
- 性能基准
- 兼容性矩阵

---

## 附录 A: 完整模块清单

### A.1 Runtime
```
agentorchestra.runtime.core
├── Config / LLMConfig / SystemConfig
├── SymphonyLLM (含 OpenAIAdapter / AnthropicAdapter / GeminiAdapter)
├── Message
├── SymphonyException / LLMException / ConfigException / ...
├── Retry (retry_with_backoff)
├── RateLimit (RateLimiter)
├── Health (HealthCheck)
├── Monitor (MonitorServer)
├── Tracing (Tracer / Span / MemoryExporter)
└── Logging / Metrics

agentorchestra.runtime.agents
├── SimpleAgent
├── ReActAgent
├── ReflectionAgent
├── PlanSolveAgent
├── LoopAgent（含认知闭环数据类）
└── Agent (基类)

agentorchestra.runtime.context
├── HistoryManager
├── TokenCounter
├── ObservationTruncator
└── ContextBuilder (GSSC)
```

### A.2 Capability
```
agentorchestra.capability.tools
├── Tool / ToolParameter / ToolResponse / ToolStatus
├── ToolRegistry (含 CircuitBreaker 集成)
├── CircuitBreaker
├── ToolFilter (ContextVars RAII)
├── Errors
└── Builtin: CalculatorTool / FileTools / MCPTool / TaskTool / SkillTool / TodoWriteTool / DevLogTool

agentorchestra.capability.memory
├── MemoryManager
├── Embedder
├── MemoryIndex / KeywordIndex
├── MemoryStore (SQLite / JSONL)
└── Summarizer

agentorchestra.capability.skills
├── SkillLoader / Skill
└── Skill (markdown + metadata)
```

### A.3 Orchestration
```
agentorchestra.orchestration.orch
├── Graph / Node / NodeContext / NodeOutput
├── AgentNode / RouterNode / MergeNode / FunctionalNode
├── GraphScheduler (含 fan-in barrier)
├── Inbox (落库消息)
└── NodeEvent / EventType

agentorchestra.orchestration.state
├── Checkpoint
├── WALEntry / Snapshot / Interrupt / Thread
├── LockRecord / IdempotencyRecord / DLQEntry / InboxMessage / AuditEntry
├── InMemoryCheckpointStore / SQLiteCheckpointStore / PostgresCheckpointStore
├── SnapshotWorker (自动 snapshot)
├── InterruptResumer (恢复中断)
└── Interfaces (10 个细粒度接口)
```

### A.4 Governance
```
agentorchestra.governance.govern
├── IdentityService (Principal / Roles)
├── ACLManager (行级 ACL)
├── PermissionChecker
└── ObjectCAS (对象级乐观锁)

agentorchestra.governance.tx
├── OptimisticLock
├── Coordinator (事务协调)
├── CompensableAction (Saga)
└── DLQManager

agentorchestra.governance.tenancy
├── TenantContext
├── TenantManager
├── TokenQuota / QuotaManager
└── Billing
```

### A.5 Ontology
```
agentorchestra.ontology
├── semantic/ - ObjectType / LinkType / Interface / Property / Vocabulary
├── kinetic/ - ActionType / Function
├── storage/ - ObjectStore / GraphStore / MaterializationTarget / Index
├── process/ - Workflow / StepNode / Transaction / Scheduler
├── governance/ - SecurityContext / Audit / Branching
└── QueryEngine
```

### A.6 Observability
```
agentorchestra.observability
├── TraceLogger (JSONL + HTML)
├── MetricsCollector (NoOp / Prometheus)
├── PrometheusTextCollector
├── OTLPHttpJsonExporter
└── SpanBatcher
```

### A.7 Components
```
agentorchestra.components
└ Components (装配门面)
```

---

## 附录 B: 关键代码片段解析

### B.1 LoopAgent 闭环执行

```python
def run(self, input_text: str) -> str:
    """完整模式：闭环认知"""
    messages = self._build_messages(input_text)
    state = LoopState(
        goal=input_text,
        plan=Plan(),
        budget=Budget(max_steps=self.max_steps, max_replans=self.max_replans),
    )
    
    while state.budget.current_steps < state.budget.max_steps:
        state.budget.current_steps += 1
        
        # 1. PLAN - 让 LLM 思考下一步
        response = self._plan(state, messages)
        
        # 2. CHECK (before act) - 是否应该终止?
        decision = self._check_done(state, bool(response.tool_calls))
        if decision.action == "stop":
            return response.content
        
        # 3. ACT - 执行工具
        tool_results = self._run_tools_sync(response.tool_calls)
        
        # 4. OBSERVE - 沉淀证据
        self._observe(tool_results, state)
        
        # 5. REFLECT - 反思进度
        if self.enable_reflection:
            reflection = self._reflect(state)
            state.reflection_history.append(reflection)
        
        # 6. CHECK (after observe) + Replan
        decision = self._check_done(state)
        if decision.action == "replan" and state.budget.current_replans < state.budget.max_replans:
            state.plan = self._replan(state)
            state.budget.current_replans += 1
```

**关键点**:
- 同步顺序执行（避免事件循环冲突）
- 异步路径保留并行（gather + Semaphore）
- 多信号终止（budget/stuck/errors/no_progress/terminate_tool）
- 反思默认关闭（向后兼容）

### B.2 分布式锁实现

```python
async def acquire_lock(self, resource_key, owner_tx, ttl_seconds):
    # 1. 查询现有锁
    row = await conn.execute(
        select(_LockRow).where(_LockRow.resource_key == resource_key)
    ).first()
    
    prev_version = 0
    prev_fencing = 0
    if row is not None:
        # 未过期 → 加锁失败
        if row.expires_at > now:
            return None
        # 过期 → 抢占
        prev_version = row.version
        prev_fencing = row.fencing_token
        await conn.execute(delete(_LockRow).where(...))
    
    # 2. 分配 fence token（单调递增）
    if dialect == "postgresql":
        seq_result = await conn.execute(
            select(func.nextval(_PG_FENCING_SEQ_NAME))
        )
        new_fencing_token = seq_result.scalar()
    elif dialect == "sqlite":
        # SQLite 模拟 sequence
        await conn.execute(
            sqlite_insert(_SequenceRow)
            .values(seq_name="fencing_token", seq_value=1)
            .on_conflict_do_update(set_={"seq_value": _SequenceRow.seq_value + 1})
        )
    
    # 3. 插入新锁（带版本号）
    new_version = prev_version + 1
    await conn.execute(
        sqlite_insert(_LockRow).values(
            resource_key=resource_key,
            version=new_version,
            fencing_token=new_fencing_token,
            ...
        ).on_conflict_do_nothing()
    )
```

### B.3 Tool 过滤（ContextVars）

```python
_disabled_tools_var = ContextVar("_disabled_tools", default=frozenset())

@contextmanager
def temporary_tool_filter(exclude: List[str]):
    """临时禁用工具（子代理隔离）"""
    current = set(_disabled_tools_var.get())
    new_set = current | set(exclude)
    token = _disabled_tools_var.set(new_set)
    try:
        yield
    finally:
        _disabled_tools_var.reset(token)

def execute_tool(self, name, args):
    if name in _disabled_tools_var.get():
        return ToolResponse(status=ToolStatus.NOT_FOUND)
    # 实际执行...
```

**为什么用 ContextVars**：
- 异步安全（每个 asyncio 任务独立）
- 嵌套支持（子代理可嵌套）
- 异常安全（ContextManager 自动 reset）

---

## 附录 C: 性能基准

| 操作 | 延迟 | 备注 |
|------|------|------|
| LLM 调用 (gpt-3.5) | 500ms - 2s | 取决于 prompt 大小 |
| 工具执行 (本地) | < 10ms | 无网络 |
| Checkpoint 保存 (内存) | < 1ms | 字典操作 |
| WAL 追加 (SQLite) | 1-5ms | 单条 INSERT |
| 锁获取 (内存) | < 0.1ms | 字典操作 |
| 锁获取 (SQLite) | 1-3ms | CAS + INSERT |

---

## 附录 D: 兼容性矩阵

| Python 版本 | 支持状态 |
|-------------|----------|
| 3.10 | ✅ |
| 3.11 | ✅ |
| 3.12 | ✅ |

| 数据库 | 支持状态 |
|---------|----------|
| SQLite | ✅ |
| PostgreSQL | ✅ |
| MySQL | ❌（未测试） |
| MongoDB | ❌ |

| LLM Provider | 支持状态 |
|--------------|----------|
| OpenAI | ✅ |
| Anthropic | ✅ |
| Gemini | ✅ |
| DeepSeek | ✅ |
| 自定义 endpoint | ✅（base_url） |

---

## 总结

Symphony v0.2.0 是一个**功能完整**的企业级多智能体编排框架：

### 优势
- ✅ **完整五层架构**: 应用/Agent/工具/本体/数据
- ✅ **5 种 Agent 范式**: 覆盖大多数业务场景
- ✅ **认知闭环**: LoopAgent 实现完整 Plan→Act→Observe→Reflect→Check→Replan
- ✅ **企业级特性**: 事务/审计/多租户/配额/治理
- ✅ **可插拔**: 横切组件通过 Components 装配
- ✅ **细粒度接口**: 10 个 CheckpointStore 子接口
- ✅ **向后兼容**: 旧 API 自动映射
- ✅ **多 Provider**: OpenAI/Anthropic/Gemini/DeepSeek
- ✅ **多存储后端**: 内存/SQLite/PostgreSQL

### 改进方向
- ⚠️ **错误处理**: 165 个 `except Exception` 待收窄
- ⚠️ **类型注解**: 65 个 `# type: ignore` 待消除
- ⚠️ **测试覆盖**: 单元/集成/压力测试已建立，需持续扩展
- ⚠️ **文档**: 各模块文档已建立，需补充示例
- ⚠️ **生态**: Skills/Tools 社区建设
- ⚠️ **可视化**: Graph 编辑器、Dashboard

### 适用场景
- ✅ 企业级 AI 应用开发
- ✅ 多 Agent 协作系统
- ✅ 复杂业务流程自动化
- ✅ 智能客服、运维、研发助手
- ⚠️ 个人玩具项目（过重）
- ⚠️ 简单单轮对话（用 SimpleAgent 即可）

---

**报告生成完毕**。本文档全面覆盖了 Symphony 框架的所有模块、功能点和知识点，期望对框架使用者、贡献者和技术决策者提供有价值的参考。