# LoopAgent 整合 Loops 技术路线（落地文档）

> 目标：将信息图表达的 **Prompt → Plan → Action → Observation → Reflection → Check → 输出/再循环** 闭环，作为**薄层抽象**叠加到现有 `LoopAgent` 上。不改变 Function-Calling while 循环的本质，仅把原本揉在一次 `invoke_with_tools + if tool_calls` 里的认知流程拆成显式阶段。
>
> 原则：**整体框架沿用现有代码实现方式**，工程能力（ToolRegistry 熔断、`asyncio.gather` 并行 + `Semaphore`、`Truncator` 截断、`trace_logger`、`StreamEvent`、`LifecycleHook`、GSSC 上下文、`max_steps`、工具注册接口）**原样复用**，逐个在阶段方法内调用。

---

## 1. 背景与定位

### 1.1 现状（代码侧）

现有 `LoopAgent` 已实现工程化的工具循环：

- `run` / `arun_stream`：同步/异步双入口
- `while current_iteration < max_steps`：基于迭代次数的循环
- `invoke_with_tools` / `ainvoke_with_tools`：Function Calling
- `_execute_tools_async`：`asyncio.gather + Semaphore(max_concurrent_tools)` 并行
- `tool_registry.async_execute_tool`：熔断 + 观测埋点
- `Truncator`：工具结果截断保护
- `trace_logger` / `StreamEvent` / `LifecycleHook`：全链路可观测
- GSSC 上下文融合、history、system prompt、tool messages
- `add_tool`(deprecated) / `register_tool` / `unregister_tool` / `list_tools` / `has_tools`

终止逻辑：**无 `tool_calls` 即停** + **达到 `max_steps` 强制停**。

### 1.2 目标（图片 Loops 思想）

将隐式循环升级为显式认知闭环：

```
Prompt → Plan → Action → Observation → Reflection → Check → 输出 / 再循环
```

新增语义能力：

- **Plan**：显式计划（结构化）
- **Observation**：工具结果沉淀为 Evidence
- **Reflection**：反思进度/缺口/风险/下一步
- **Check**：多信号终止判定（取代单一"无 tool_calls 就停"）
- **Replan**：不满足目标时回到 Plan

### 1.3 非目标

- 不改变 LLM / 工具的调用契约
- 不破坏现有事件、钩子、日志协议
- 不强制存量调用方升级（特性开关默认关闭）

---

## 2. 总体架构

### 2.1 分层

```
┌─────────────────────────────────────────────────────┐
│                 入口层 (Facade)                      │
│   run()  /  stream_run()  /  arun()  /  arun_stream() │
└──────────────────────┬──────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────┐
│             循环编排层 (Loop Orchestrator)            │
│  _run_loop() 同步  /  _arun_loop() 异步              │
│                                                      │
│   Plan → Act → Observe → Reflect → Check → Replan   │
└──────────────────────┬──────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────┐
│              阶段方法层 (Stage Methods)              │
│  _plan()  _act()  _observe()  _reflect()            │
│  _check_done()  _replan()                            │
└──────────────────────┬──────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────┐
│            既有工程能力层 (复用，不动)                │
│  ToolRegistry(async_execute_tool + 熔断)             │
│  asyncio.gather + Semaphore                          │
│  Truncator / trace_logger / StreamEvent             │
│  LifecycleHook / GSSC / history / system prompt      │
└─────────────────────────────────────────────────────┘
```

关键：**阶段方法层只做编排与语义沉淀，所有真实执行仍委托给既有工程能力层。**

### 2.2 核心抽象：`LoopState`

让隐式的 `messages + iteration` 升级为结构化的运行状态（对应图片"记忆存储 / 状态管理 / 成本控制"）。

```python
@dataclass
class LoopState:
    goal: str                          # 用户目标（来自 input_text / system_prompt）
    plan: Plan                         # 结构化计划
    evidence: List[Evidence]           # 每轮工具结果沉淀
    budget: Budget                     # max_steps / max_replans / 已用资源
    status: LoopStatus                 # running / stopped / replanning / error
    reflection_history: List[Reflection]
    last_decision: Optional[TerminationDecision] = None
```

```python
@dataclass
class Plan:
    steps: List[str]
    current_step: int
    open_questions: List[str]
    success_criteria: List[str]

@dataclass
class Evidence:
    tool_name: str
    tool_call_id: str
    status: str                        # success / error / partial
    truncated: bool
    summary: str
    supports_goal: bool
    contradictions: List[str]
    next_info_gap: Optional[str]

@dataclass
class Reflection:
    progress: float                    # 0~1
    issues: List[str]
    next_strategy: Optional[str]
    should_replan: bool

@dataclass
class Budget:
    max_steps: int
    max_replans: int
    current_steps: int
    current_replans: int
```

`messages` **仍然喂给 LLM**（兼容现有协议）；`LoopState` **做工程判断**（Plan/Evidence/Budget/Status）。两者职责分离。

---

## 3. 阶段映射（图片 → 代码）

| 图片阶段 | 代码方法 | 说明 | 复用既有能力 |
|---|---|---|---|
| **Prompt** | `_build_messages` | system + history + GSSC + user | 原样保留 |
| **Plan（制定计划）** | `_plan()` | LLM 生成/更新结构化 Plan | `invoke_with_tools` |
| **Action（执行任务）** | `_act()` | 执行工具调用 | `_execute_tools_async`：并行 + Semaphore + registry 熔断 + Truncator |
| **Observation（验证结果）** | `_observe()` | 工具结果 → `Evidence` | trace_logger 事件 |
| **Reflection（反思/状态管理）** | `_reflect()` | 模型反思，失败降级规则反思 | LLM invoke |
| **Check（验证器+终止条件）** | `_check_done()` | 多信号裁决 | `terminate` 工具 + goal checker + stuck + errors + budget |
| **再循环箭头** | `_replan()` | 更新 Plan，回到 `_plan` | LoopState.plan |
| **输出** | `final_response` | 返回最终文本 | 原逻辑 |

### 3.1 控制流（状态机）

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

状态转换：`LoopStatus = {running, stopped, replanning, error}`。

---

## 4. 各阶段设计

### 4.1 Plan（`_plan`）

- **同步路径**：`self.llm.invoke_with_tools(messages, tools, tool_choice="auto")`
- **结构化增强**：可选 system prompt 引导模型输出 Plan schema（steps / success_criteria）；不强求模型严格遵从，保持兼容性
- **首次调用**：基于 `goal` 生成 Plan；**后续调用**：基于 `Reflection` 更新 Plan（Replan）

```python
def _plan(self, state: LoopState, messages):
    response = self.llm.invoke_with_tools(messages, tools=self._build_tool_schemas(), tool_choice="auto")
    # 尝试从 response 抽取结构化 plan；失败则保持上一轮 plan
    state.plan = self._parse_plan(response) or state.plan
    return response
```

### 4.2 Act（`_act`）

**同步/异步彻底分流**（避免事件循环死锁，见第 7 节坑点）：

- **异步路径 `_arun_loop`**：`await _execute_tools_async(...)` → `asyncio.gather + Semaphore` 并行（**并行能力保留**）
- **同步路径 `_run_loop`**：顺序执行，直接调用 func + 手动复刻熔断/截断语义（**永不碰 asyncio，不死锁**）

真实逻辑落地：

```python
# 异步（保留并行）
tool_results = await self._execute_tools_async(tool_calls, current_iteration, on_tool_call)
# 同步（顺序，避免事件循环冲突）
for tc in tool_calls:
    result = self._run_tool_sync(tc, current_iteration)  # func + 截断 + 错误封装
    tool_results.append(result)
```

### 4.3 Observe（`_observe`）

工具结果同时沉淀为 `Evidence`，追加到 `state.evidence`，并保留既有 `messages.append(role=tool)`。

```python
def _observe(self, tool_results, state: LoopState):
    for name, call_id, result_dict in tool_results:
        evidence = Evidence(
            tool_name=name, tool_call_id=call_id,
            status="error" if result_dict.get("error") else "success",
            truncated=result_dict.get("truncated", False),
            summary=self._summarize(result_dict["content"]),
            supports_goal=self._assess_support(result_dict, state.goal),
            contradictions=[], next_info_gap=None,
        )
        state.evidence.append(evidence)
        messages.append({"role": "tool", "tool_call_id": call_id, "content": result_dict["content"]})
    return messages
```

既有 `trace_logger.log_event("tool_result", ...)` 原样保留。

### 4.4 Reflect（`_reflect`）

- `_model_reflect`：基于 `goal + plan + evidence` 构造反思 prompt → LLM → `_parse_reflection`
- **降级**：LLM 失败 → `_rule_based_reflection`（基于 stuck/errors/evidence 的规则判断）
- **开关**：`enable_reflection=False` 时为 no-op，行为与改造前一致

```python
def _reflect(self, state: LoopState) -> Reflection:
    if not self.enable_reflection:
        return Reflection(progress=0.0, issues=[], next_strategy=None, should_replan=False)
    try:
        return self._model_reflect(state)
    except Exception:
        return self._rule_based_reflection(state)  # 永不死锁
```

### 4.5 Check（`_check_done`）：多信号终止

**核心升级**：从"无 tool_calls 即停"升级为五信号统一裁决。

```python
@dataclass
class TerminationDecision:
    signal: str        # completed / stuck / errors / budget / no_progress / terminate_tool
    action: str        # stop / replan / continue
    reason: str

def _check_done(self, state: LoopState, has_tool_calls: bool) -> TerminationDecision:
    # 1) 显式 terminate 工具
    if self._explicit_terminate():
        return TerminationDecision("terminate_tool", "stop", "model called terminate")
    # 2) 业务目标达成
    if self._is_goal_met(state):
        return TerminationDecision("completed", "stop", "goal satisfied")
    # 3) 预算耗尽
    if state.budget.current_steps >= state.budget.max_steps:
        return TerminationDecision("budget", "stop", "max_steps reached")
    # 4) 卡死检测（连续重复调用）
    if self._is_stuck(state):
        return TerminationDecision("stuck", "replan", "repeated identical calls")
    # 5) 连续错误
    if self._consecutive_errors(state) >= self.max_consecutive_errors:
        return TerminationDecision("errors", "stop", "too many tool errors")
    # 6) 无进展 + 无工具调用
    if not has_tool_calls and state.evidence:
        return TerminationDecision("no_progress", "stop", "no further action")
    return TerminationDecision("running", "continue", "")
```

**优先级**：`terminate_tool / completed / budget / errors / stuck → replan / no_progress / running → continue`。

### 4.6 Replan（`_replan`）

- 调用 `_model_replan` 更新 `state.plan`（剩余步骤、open_questions）
- **受约束**：`current_replans < max_replans`，否则强制 stop
- 更新后回到 `_plan` 下一轮

---

## 5. 终止与再规划策略

### 5.1 终止信号汇总

| 信号 | 触发条件 | 默认动作 | 可配置 |
|---|---|---|---|
| `terminate_tool` | 模型调用内置 `terminate` 工具 | stop | 必选 |
| `completed` | `_check_goal` 业务规则满足 | stop | 必选 |
| `budget` | `current_steps ≥ max_steps` | stop | 必选 |
| `errors` | 连续工具错误 ≥ `max_consecutive_errors`(默认 3) | stop | 可配 |
| `stuck` | 连续 N 轮调用相同工具+参数 | replan | 可配 N |
| `no_progress` | 无 tool_calls 且已有 evidence | stop | 必选 |
| `running` | 以上均不满足 | continue | — |

### 5.2 Replan 约束

- `max_replans` 上限（默认 2），防无限换计划
- Replan 次数计入 Budget
- 连续 replan 仍无进展 → 强制 stop
- Replan 后 Plan 变更写入 trace，便于回放

### 5.3 内置工具：`terminate`

`_build_tool_schemas` 在复用基类 schemas 基础上**追加内置 `terminate` 工具**，让模型可显式声明"任务完成"，对应图片「终止条件」。

```json
{
  "type": "function",
  "function": {
    "name": "terminate",
    "description": "Call when the task goal has been fully satisfied.",
    "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": []}
  }
}
```

---

## 6. 可观测与事件协议

在既有事件基础上**追加**阶段事件，不破坏前端：

```
AGENT_START
  └─ PLAN_START / PLAN_FINISH
  └─ ACT_START / TOOL_CALL_FINISH / ACT_FINISH
  └─ OBSERVE_FINISH
  └─ REFLECT_FINISH (含 progress / should_replan)
  └─ CHECK_FINISH (含 signal / action / reason)
  └─ REPLAN_FINISH (if replan)
  └─ AGENT_FINISH / ERROR
```

每条事件携带 `iteration`、`loop_state` 摘要（plan.current_step、evidence 数量、budget 余量）。

既有 `trace_logger` 字段扩展（向后兼容）：新增 `plan / evidence / reflection / decision` 字段，旧字段保留。

---

## 7. 关键技术问题与解法

### 7.1 同步/异步死锁风险（重要）

**坑点**：同步 `_act` 若复用 `asyncio.run(_execute_tools_async(...))`，在"调用方已有事件循环"时抛 `RuntimeError: asyncio.run() cannot be called from a running event loop`；改用 `run_coroutine_threadsafe` 则**同 loop 死锁**（实测超时）。

**解法**：同步/异步彻底分流（见 4.2）——同步顺序执行 + 手动复刻熔断/截断；异步独占 `gather + Semaphore`。**并行能力不丢失、同步不阻塞、语义一致。**

### 7.2 流式文本与 tool_calls 一致性

既有实现先流式累积 `full_response`，再 `ainvoke_with_tools` 取 `tool_calls`。整合后保持一致，并在 `_plan` / `_check_done` 间明确：流式 chunk 用于前端展示，`tool_calls` 解析用于阶段推进，两者来源同一 LLM 响应。

### 7.3 Reflection 开销控制

- 默认 `enable_reflection=False`（存量零开销）
- 可配置反思频率：每轮 / 隔轮 / 仅错误后 / 仅 stuck 时
- 规则反思降级，避免额外 LLM 调用

### 7.4 向后兼容

- `run` / `arun_stream` 签名不变
- `enable_reflection` / `enable_replan` **默认关闭** → 存量行为完全一致
- 新事件在既有事件基础上追加
- 工具接口 `add_tool`(deprecated) / `register_tool` 等保留

---

## 8. 增量落地步骤

推荐**分阶段、可验证、零退化**迁移：

### 阶段 0：抽公共循环（基线保持）
- 抽取 `_run_loop`（同步）/ `_arun_loop`（异步），把现有 while 逻辑整体迁入
- ✅ 验证：现有测试用例全部通过，行为零变化

### 阶段 1：Check 增强（先加稳定终止）
- 实现 `_check_done` 五信号 + `_is_stuck` + `terminate` 工具
- ✅ 验证：stuck / budget / terminate 三个场景覆盖

### 阶段 2：Observe + Evidence
- 工具结果沉淀 `Evidence`，接入 trace
- ✅ 验证：evidence 字段正确、trace 兼容

### 阶段 3：Reflect + Replan
- `_reflect`（模型 + 规则降级）、`_replan`、`max_replans`
- 默认关闭，灰度开启
- ✅ 验证：replan 后 Plan 更新、次数受限

### 阶段 4：Plan 结构化
- 结构化 Plan schema + `_parse_plan`
- ✅ 验证：首次/后续 Plan 生成正确

### 阶段 5：可观测完善
- 追加阶段事件、loop_state 摘要、回放能力
- ✅ 验证：前端事件流、trace 回放

每个阶段用既有 trace 做**前后对比**，确保零退化。

---

## 9. 验证计划

### 9.1 单元测试（pytest）

| 用例 | 验证点 |
|---|---|
| `test_terminate_tool_stops` | 模型调用 terminate → 立即 stop |
| `test_max_steps_budget` | 达 max_steps → budget 信号 stop |
| `test_stuck_detection_replan` | 连续相同调用 → stuck → replan |
| `test_max_replans_cap` | replan 达上限 → 强制 stop |
| `test_consecutive_errors_stop` | 连续错误 ≥ 阈值 → stop |
| `test_reflection_fallback` | LLM 反思失败 → 规则反思不抛错 |
| `test_no_tool_calls_stops` | 无 tool_calls → no_progress stop |
| `test_goal_met_checker` | 自定义 `_check_goal` 满足 → stop |
| `test_parallel_tools_async` | 异步路径保留并行（gather + Semaphore） |
| `test_sync_no_event_loop_deadlock` | 同步路径在已有 loop 下不死锁 |
| `test_backward_compat_disabled` | 开关关闭时行为 = 改造前 |

### 9.2 端到端场景

1. **Demo1 正常终止**：Plan → Act → Observe → Reflect → Check → terminate → stop ✅
2. **Demo2 卡死恢复**：重复调用 → stuck → replan → 新策略 ✅
3. **Demo3 流式事件**：`arun_stream` 完整产出 PLAN/ACT/OBSERVE/REFLECT/CHECK/REPLAN ✅
4. **Demo4 预算耗尽**：长任务达 max_steps → 带状态输出 ✅
5. **Demo5 向后兼容**：开关关闭，与旧版输出一致 ✅

### 9.3 评估指标

- **任务成功率**（goal 达成率）
- **平均迭代步数**（是否因 Reflect/Replan 收敛更快）
- **卡死恢复率**（stuck → replan → 成功比例）
- **终止准确率**（该停就停，不早停/不停）
- **额外 LLM 开销**（Reflection 成本）
- **P95 延迟 / token 消耗**
- **trace 回放完整性**

---

## 10. 接入既有工程的迁移清单

落地文件中的 `Agent / SymphonyLLM / ToolRegistry / Truncator / TraceLogger / StreamEvent` 为轻量桩（用于脱离工程独立运行）。接入时：

1. 删除桩类，替换为工程真实 import
2. `_run_tool_sync` 的熔断逻辑改回调用 registry 真实同步接口；异步路径 `_execute_tools_async` 已与原文一致，**无需改动**
3. 删除 `FakeLLM` / `main()` demo 部分
4. 事件名映射：若工程事件枚举不同，在门面层做名称适配（不影响内部逻辑）
5. `LoopState` 序列化：如需跨进程，为 dataclass 加 `to_dict / from_dict`

---

## 11. 效果评估结论

整合后效果定位：

- **结构/流程**：基本对齐图片，显式闭环完整 ✅
- **工程可靠性**：优于图片表达（熔断/截断/并发/可观测/日志）✅
- **语义智能（Plan/Reflect/Check）**：是对图片的**受控近似**，取决于模型质量、prompt、goal checker、evidence 设计 ⚠️
- **控制流等价性**：高；**语义效果等价性**：中-高

**结论**：方向正确。重点不是继续加"阶段名称"，而是把 **Plan / Observe / Reflect / Check 的质量做到可观测、可评估、可回放**——从"像 Loops"升级为"稳定地按 Loops 工作"。

下一步优先级建议：

1. 结构化 Plan / Evidence schema（第 4.1、4.3）
2. Observation 语义抽取（supports_goal / contradictions / info_gap）
3. Check 多信号评分（goal_coverage / evidence_support / completeness）
4. Replan 限频限次
5. 最终输出前 Answer Validation
6. trace 回放 + 评估集

---

## 附录 A：完整控制流伪代码

```python
async def _arun_loop(self, input_text, **kwargs):
    state = LoopState(goal=input_text, plan=Plan(...), budget=Budget(max_steps=self.max_steps, max_replans=2))
    messages = self._build_messages(input_text)

    while state.budget.current_steps < state.budget.max_steps:
        state.budget.current_steps += 1

        # PLAN
        response = await self._plan(state, messages)
        tool_calls = response.tool_calls

        # CHECK (before act: terminate? goal met?)
        decision = self._check_done(state, bool(tool_calls))
        if decision.action == "stop":
            return self._finalize(state, response)

        # ACT
        tool_results = await self._execute_tools_async(tool_calls, state.budget.current_steps, ...)

        # OBSERVE
        messages = self._observe(tool_results, state)

        # REFLECT
        reflection = self._reflect(state)
        state.reflection_history.append(reflection)

        # CHECK (after observe)
        decision = self._check_done(state, bool(tool_calls))
        if decision.action == "stop":
            return self._finalize(state, response)
        if decision.action == "replan" and state.budget.current_replans < state.budget.max_replans:
            state.plan = self._replan(state, reflection)
            state.budget.current_replans += 1
            continue

    return self._finalize(state, response)  # max_steps 兜底
```

## 附录 B：配置项一览

| 配置 | 默认 | 说明 |
|---|---|---|
| `enable_reflection` | False | 是否启用反思（灰度） |
| `enable_replan` | False | 是否启用再规划 |
| `max_steps` | 5 | 最大迭代步数 |
| `max_replans` | 2 | 最大再规划次数 |
| `max_concurrent_tools` | 3 | 并行工具数 |
| `stuck_threshold` | 2 | 连续重复调用判为卡死 |
| `max_consecutive_errors` | 3 | 连续错误上限 |
| `reflection_interval` | 1 | 反思频率（每 N 步） |
| `truncator` | — | 工具结果截断器 |


