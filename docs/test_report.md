# AgentOrchestra 全面压力测试报告

> 生成时间：2026-09-04 17:44:07  
> 测试框架：AgentOrchestra  
> 结果：**26/26 通过**，失败 0

## 一、工具清单总览

| 来源 | 数量 | 工具 |
|------|------|------|
| 自定义工具 | 3 | Weather, Discount, python_calculator |
| ontology 添加 | 4 | QueryCustomer, QueryOrder, create_order, CallComputeOrderTotal |
| Agent 自动注册 | 4 | Skill, Task, TodoWrite, DevLog |
| 测试专用 | 2 | AlwaysFail, BigOutput |
| **总计** | **13** | |

> 注意：`Skill/Task/TodoWrite/DevLog` 是 **Agent 初始化时自动注册** 的框架工具；
> `QueryCustomer/QueryOrder/create_order/CallComputeOrderTotal` 是 **ontology `engine.mount(registry)` 生成** 的工具。

## 二、测试用例明细

| # | 测试名 | Agent 类型 | 任务 | 使用的工具 | 结果 |
|---|--------|-----------|------|-----------|------|
| 1 | Agent类型工厂 | factory | 创建4种类型Agent | — | ✅ |
| 2 | 子代理工厂 | react(subagent) | 验证子代理类型 | — | ✅ |
| 3 | 主Agent-创建订单 | ReActAgent | 帮我创建订单 | create_order | ✅ |
| 4 | 主Agent-查天气-北京 | ReActAgent | 查一下北京天气 | Weather | ✅ |
| 5 | 主Agent-查天气-东京 | ReActAgent | 查一下东京天气 | Weather | ✅ |
| 6 | 主Agent-计算 | ReActAgent | 帮我计算一下 2**10 | python_calculator | ✅ |
| 7 | 主Agent-写日志 | ReActAgent | 写一条审计日志 | DevLog | ✅ |
| 8 | 主Agent-加载技能 | ReActAgent | 加载 systematic-debugging 技能 | Skill | ✅ |
| 9 | 主Agent-折扣 | ReActAgent | 算一下 vip 折扣 | Discount | ✅ |
| 10 | run_as_subagent-查上海天气 | ReActAgent(子代理=自身实例) | 查上海天气 | Weather | ✅ |
| 11 | run_as_subagent-帮我计算 5*5 | ReActAgent(子代理=自身实例) | 帮我计算 5*5 | python_calculator | ✅ |
| 12 | run_as_subagent-加载 xlsx 技能 | ReActAgent(子代理=自身实例) | 加载 xlsx 技能 | Skill | ✅ |
| 13 | Task工具 | TaskTool->子代理 | 用 Task 工具派发子任务 | Task | ✅ |
| 14 | 工具高频调用 | direct | 7500次混合工具调用 | CallComputeOrderTotal, Discount, QueryOrder, Weather, create_order, python_calculator | ✅ |
| 15 | 工具并发调用 | direct | 8线程并发混合工具 | CallComputeOrderTotal, Discount, QueryOrder, Weather, create_order, python_calculator | ✅ |
| 16 | 熔断器触发 | direct | 连续失败4次触发熔断 | AlwaysFail | ✅ |
| 17 | 历史截断/压缩 | ReActAgent | 40轮对话注入触发压缩 | — | ✅ |
| 18 | 工具输出截断 | ObservationTruncator | 5000行输出触发截断 | BigOutput | ✅ |
| 19 | ontology-对象 | OntologyEngine | 对象CRUD | QueryCustomer, QueryOrder | ✅ |
| 20 | ontology-查询引擎 | OntologyEngine | object_set/join | QueryCustomer, QueryOrder | ✅ |
| 21 | ontology-规则 | OntologyEngine | 规则校验 | create_order | ✅ |
| 22 | skills加载 | SkillTool | 加载6个技能 | Skill | ✅ |
| 23 | skills在Agent中 | ReActAgent | Agent按需加载xlsx技能 | Skill | ✅ |
| 24 | 工作流 | Workflow | 1000次双节点工作流 | log_step | ✅ |
| 25 | 事务补偿 | Transaction | 1000次事务含失败补偿 | deduct_stock, fail_op | ✅ |
| 26 | TraceLogger | observability | 5000条事件落盘 | — | ✅ |

## 三、关键机制验证详情

### 3.1 熔断器机制

**触发条件**：工具连续失败达到 `circuit_failure_threshold`（默认 3 次）后，熔断器从 CLOSED → OPEN。
熔断期间调用该工具返回 `CIRCUIT_OPEN` 错误；经过 `circuit_recovery_timeout`（默认 300 秒）后自动恢复。

| 阶段 | 状态 | 说明 |
|------|------|------|
| 第1-3次调用 | error | 连续失败，未熔断 |
| 第4次调用 | error（熔断OPEN） | 达到阈值，熔断开启 |
| 熔断后调用 | CIRCUIT_OPEN | 直接拒绝，不执行 |
| 等待恢复后 | CLOSED | 恢复超时后自动闭合 |

> 测试验证：阈值 3 次，恢复 1 秒，第 4 次触发 OPEN，熔断开启状态 `tripped=True`。

### 3.2 截断机制（两类）

**A. 历史截断/压缩**：历史 Token 数超过 `context_window × compression_threshold`（默认 128000×0.8）时触发。
使用简单摘要（统计信息）或智能摘要（LLM 生成），按 `min_retain_rounds` 保留最近轮次。

> 测试验证：context_window=2000, threshold=0.5 → 阈值 1000 tokens。
注入 40 轮（80 条消息，1772 tokens）后压缩为
**5 条，169 tokens**，保留最近轮次。

**B. 工具输出截断**：工具输出超过 `tool_output_max_lines`（默认 2000 行）或 `tool_output_max_bytes`（默认 50KB）时，
`ObservationTruncator` 按方向（head/tail/head_tail）截断，完整输出保存到文件。

> 测试验证：BigOutput 输出 5000 行 → 按 head_tail 保留 2001 行，完整输出落盘。

### 3.3 主 Agent 与子 Agent 类型

| 角色 | 类型 | 说明 |
|------|------|------|
| 主 Agent | **ReActAgent** | 推理-行动循环，负责订单/天气/计算/日志/技能等任务 |
| 子代理（run_as_subagent） | **同类型实例** | 上下文隔离模式：清空历史→执行→恢复，不污染主上下文 |
| 子代理（Task 工具） | **default_subagent_factory** | 按 agent_type 创建 react/reflection/plan/simple 子代理 |

框架共支持 4 种 Agent 类型：`react`（ReActAgent）、`reflection`（ReflectionAgent）、
`plan`（PlanSolveAgent）、`simple`（SimpleAgent），由 `create_agent()` 工厂创建。

### 3.4 Ontology 使用位置

1. **建模**：`ObjectType`（customer/order）、`LinkType`（belongs_to）、`ActionType`（create_order）、`Function`（compute_order_total）
2. **挂载**：`engine.mount(registry)` 生成 4 个工具（QueryCustomer/QueryOrder/create_order/CallComputeOrderTotal）
3. **存储**：`ObjectStore` + `GraphStore` 提供 insert/get/filter
4. **查询引擎**：`QueryEngine` 提供 object_set（集合查询）、条件过滤、链接导航
5. **规则治理**：动作 `rules` 校验（如金额必须为正），违规返回 `{'success': False, 'errors': [...]}`
6. **工作流/事务**：`Workflow`（多节点 DAG）、`Transaction`（失败自动补偿）

### 3.5 Skills 使用位置

1. **启动时**：`SkillLoader` 扫描 `skills/` 目录，仅加载元数据（渐进式披露 Layer 1）
2. **按需加载**：Agent 通过 `Skill` 工具加载完整 `SKILL.md` body（Layer 2）
3. **资源提示**：列出 scripts/references/examples 目录文件（Layer 3）
4. **参数替换**：`$ARGUMENTS` 占位符替换

> 本测试下载并加载 6 个真实技能：skill-creator、systematic-debugging、test-driven-development、verification-before-completion、writing-plans、xlsx，全部加载成功。

## 四、性能指标

| 测试 | 指标 |
|------|------|
| 工具高频调用 | 5100 次混合调用，0.14s |
| 工具并发调用 | 8线程并发，0.03s，0 错误 |
| 工作流 | 1000 次双节点，0.19s |
| 事务补偿 | 1000 次事务，补偿 500 次 |
| TraceLogger | 5000 事件，0.89s，874KB |
| skills加载 | 6 技能全部 success |

## 五、结论

全部 **26/26** 项测试通过。框架在工具执行、Agent 编排、
熔断保护、历史截断、工具输出截断、本体建模、技能加载、工作流事务、可观测性等全链路表现稳定。
