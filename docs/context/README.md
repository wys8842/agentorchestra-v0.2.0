# context：上下文工程（历史 / Token 预算 / 截断 / GSSC）

> 本模块（`agentorchestra.context`，规范路径 `agentorchestra.runtime.context`）为 Agent 提供对话历史管理、Token 预算估算、工具输出截断与 GSSC 上下文构建四类能力，属于运行时域的可插拔上下文组件。

## 设计动机与原则

1. **按能力拆分文件，单一职责**。源码把"上下文工程"拆成 4 个独立文件：`history.py`（历史管理）、`token_counter.py`（Token 预算）、`truncator.py`（工具输出截断）、`builder.py`（GSSC 构建流水线）。每份职责互不重叠，可独立测试、独立替换。
2. **组件化装配，业务解耦**。`runtime.core.agent.base` 的 `Agent.__init__` 在运行时按 `Config` 组装 `HistoryManager` / `ObservationTruncator` / `TokenCounter` / `ContextBuilder` 到 `self` 上（见 `base.py`），Agent 业务代码不直接 new 这些类，只通过 `history_manager`、`truncator`、`_build_context` 等名字使用，因此上层换实现不影响 Agent 逻辑。
3. **只追加、不编辑的历史模型**。`HistoryManager` 只提供 `append`，压缩也通过"摘要消息 + 保留最近 N 轮"的整体替换实现，天然缓存友好、可序列化、可恢复（`Message.to_dict/from_dict` 双向支持）。
4. **本地估算优先，无网络依赖**。Token 计数基于 `tiktoken` 的本地编码（`encoding_for_model` → `cl100k_base` 兜底），缺库或编码失败时按"1 token ≈ 4 字符"降级，绝不把计数做成一次 API 往返。
5. **预算内执行**。所有面向 LLM 的组装都以 `Config.history.context_window` / `ContextConfig.max_tokens` 等预算为上限，压缩、筛选、截断都在预算内进行，避免 prompt 无限膨胀。
6. **缓存 + 指纹做增量计算**。`TokenCounter` 以"内容 hash → token 数"缓存，并维护"基线计数 + 批指纹"，`count_incremental` 只在列表真的变化时重算，长会话不需要每次全量扫历史。
7. **显式 GSSC 四阶段流水线**。`ContextBuilder` 把构建过程显式分为 Gather（多源收集）→ Select（相关性/新近性/预算筛选）→ Structure（固定模板）→ Compress（超预算截断）四步，便于观测与扩展；知识来源通过 `knowledge_provider` 回调注入，而不是硬编码检索实现。
8. **演进收敛**。曾内置的 MemoryTool / RAGTool 检索工具已移除（文件头注明），相应职责收敛为 `knowledge_provider` 回调与 `additional_packets`；压缩方向也保留 `enable_compression=False` 的关闭开关，避免隐式行为改变调用方结果。

## 设计优势

- Agent 侧零成本获得历史压缩、预算管理、输出截断能力：只要构造 `Agent` 就会自动装配好 `history_manager/truncator/token_counter`。
- Token 计算本地且带缓存，调用次数为 O(新增消息)，不随历史增长变慢。
- 输出截断统一由 `ObservationTruncator` 处理并归档完整输出，各工具不必各自实现截断。
- 历史可一键落盘/恢复（`to_dict/load_from_dict`），天然支撑会话保存（`Agent.save_session` 亦复用 `Message` 序列化）。
- 显式四阶段流水线让"把什么喂给模型"可观测、可调参、可替换实现。

## 模块构成

物理路径 | 子模块职责 | 主要公开导出
--- | --- | ---
`agentorchestra/runtime/context/__init__.py` | 包级聚合导出 | `ContextBuilder`、`ContextConfig`、`ContextPacket`、`HistoryManager`、`ObservationTruncator`、`TokenCounter`
`agentorchestra/runtime/context/history.py` | 历史消息的追加、轮次估算、压缩、序列化 | `HistoryManager`
`agentorchestra/runtime/context/token_counter.py` | 本地 Token 估算（缓存 + 增量 + 降级） | `TokenCounter`
`agentorchestra/runtime/context/truncator.py` | 工具输出多方向截断与完整输出归档 | `ObservationTruncator`
`agentorchestra/runtime/context/builder.py` | GSSC 上下文构建流水线及相关数据结构 | `ContextBuilder`、`ContextConfig`、`ContextPacket`，以及模块级 `count_tokens(text)`、`TFIDFRanker`（未进包级 `__all__`，可按模块路径导入）

> 说明：包文档字符串中提到的 `Compactor` / `NotesManager` / `ContextObserver` 在仓库中尚无对应实现，不在公开 API 内，使用前请以本表与实际 `__all__` 为准。

## 功能清单

### 1. HistoryManager —— 历史管理（`history.py`）

- 是什么：对话消息的有序容器，遵循"只追加不编辑"，并把"压缩"建模为 `summary` 摘要消息 + 保留最近完整轮次。
- 解决什么：长会话无限增长、历史无法序列化、压缩边界不清。
- 关键 API：
  - `HistoryManager(min_retain_rounds=10, compression_threshold=0.8)`：构造时两个参数分别控制压缩后保留的完整轮次数与压缩阈值（阈值当前预留，未参与判断）。
  - `append(message: Message) -> None`、`get_history() -> List[Message]`（返回副本）、`clear()`。
  - `estimate_rounds() -> int`：以"1 条 user 消息 + 其后若干条非 user 消息"为一轮做估算。
  - `find_round_boundaries() -> List[int]`：返回每条 user 消息的起始下标。
  - `compress(summary: str) -> None`：轮次数 ≤ `min_retain_rounds` 时不动作；否则把历史替换为一条 `Message(content=f"## Archived Session Summary\n{summary}", role="summary", metadata={"compressed_at": ...})` + 最近 N 轮。
  - `to_dict() -> Dict` / `load_from_dict(data) -> None`：序列化（含 `history`、`created_at`、`rounds`）与恢复。
- 行为与边界：`role="summary"` 消息会被下游 Agent 以"system + `[历史摘要]` 前缀"形式注入（见 `SimpleAgent._build_messages`），因此摘要消息不会被当作普通 user/assistant 消息发送给模型。

### 2. TokenCounter —— Token 预算估算（`token_counter.py`）

- 是什么：不调用模型、纯本地的消息 Token 估算器，带按内容 hash 的缓存与增量计数状态。
- 解决什么：预估 prompt 成本、触发历史压缩、避免每轮全量重算。
- 关键 API：
  - `TokenCounter(model="gpt-4")`：按模型名选编码；类常量 `ROLE_OVERHEAD = 4`，每条消息角色开销计入。
  - 全量：`count_message(message) -> int`（走缓存、含角色开销）、`count_messages(messages) -> int`、`count_text(text) -> int`（不走缓存、无角色开销）。
  - 增量：`set_baseline(count, messages=None)`、`begin_session(messages) -> int`、`count_incremental(new_messages=None, previous_fingerprint=None) -> int`、`append_and_count(message) -> int`。
  - 缓存与状态：`clear_cache()`、`get_cache_size()`、`get_cache_stats()`、`get_state()`、`reset_state()`。
- 行为与边界：
  - 指纹取批消息前 100 条 "role:内容hash" 的 MD5 前 16 位；`count_incremental` 在传入 `previous_fingerprint` 且不匹配时全量重算，否则走增量。
  - `tiktoken` 不可用 / 模型编码未知时自动降级为 `len(text)//4` 字符估算，不抛异常。

### 3. ObservationTruncator —— 工具输出截断（`truncator.py`）

- 是什么：统一截断超长工具输出（行数/字节双上限），支持 `head` / `tail` / `head_tail` 三种方向，并把完整输出写盘归档。
- 解决什么：单个工具输出动辄上万行会撑爆上下文；每个工具各自截断会产生不一致行为。
- 关键 API：
  - `ObservationTruncator(max_lines=2000, max_bytes=51200, truncate_direction="head", output_dir="tool-output")`。
  - `truncate(tool_name: str, output: str, metadata=None) -> Dict`：返回 `{"truncated": bool, "preview": str, "full_output_path": Optional[str], "stats": {...}}`。未超限时 `preview` 即原文、`full_output_path=None`；超限时按方向截行，若仍超字节则再按 UTF-8 字节截断（`errors='ignore'`），并 `_save_full_output` 把 `{tool, output, timestamp, metadata}` 写成 JSON 文件。
- 行为与边界：`Agent` 基类会用它包住 `_execute_single_tool_call` 的结果，异步 ReAct 工具路径只在 `tool_response.status.value != "error"` 时截断；截断动作内部 `try/except` 兜底，失败不影响主流程。

### 4. ContextBuilder / ContextConfig / ContextPacket —— GSSC 流水线（`builder.py`）

- 是什么：Gather-Select-Structure-Compress 四阶段上下文组装器，输出一份结构化上下文字符串。
- 解决什么：多路信息（系统指令、知识检索、对话历史、任务状态）如何按预算、按相关性组织进 prompt。
- 关键 API：
  - `ContextConfig(max_tokens=8000, reserve_ratio=0.15, min_relevance=0.3, enable_mmr=True, mmr_lambda=0.7, system_prompt_template="", enable_compression=True)`；`get_available_tokens()` 返回扣除余量的预算。
  - `ContextPacket(content, timestamp=now, metadata=None, token_count=0, relevance_score=0.0)`：构造后自动 `count_tokens`。
  - `ContextBuilder(config=None, knowledge_provider=None)`；`build(user_query, conversation_history=None, system_instructions=None, additional_packets=None) -> str`。
  - 模块级 `count_tokens(text) -> int` 与 `TFIDFRanker`（内存 TF-IDF：`fit(vectorize)/cosine_similarity`）。
- 行为与边界：
  - Gather 只保留最近 10 条历史作为 `[Context]`；Select 采用 0.6×相关性 + 0.3×新近性（1 小时指数衰减）+ 0.1×重要性的复合分，`knowledge_base` 类型包不参与相关性过滤、`instructions` 类型固定纳入，按预算贪心填充；Structure 生成 `[Role & Policies]/[Task]/[State]/[Evidence]/[Context]/[Output]` 模板；Compress 在超预算时按行保留前部内容。
  - 源码注记该文件曾依赖已移除的 MemoryTool/RAGTool，构造函数会 `tiktoken.get_encoding("cl100k_base")`，需要环境中存在 `tiktoken`（pyproject 已列为核心依赖）；`Agent` 默认不启用它，需 `config.context_builder.enabled=True`。

## 使用说明

导入（经典名 = 兼容别名，与规范名指向同一模块对象）：

```python
# 经典扁平名（优先示例）
from agentorchestra.context import HistoryManager, TokenCounter, ObservationTruncator
# 规范路径（推荐新代码）
from agentorchestra.runtime.context import HistoryManager, TokenCounter, ObservationTruncator
from agentorchestra.runtime.context.builder import ContextBuilder, ContextConfig, ContextPacket
```

分场景示例：

```python
# 1) 历史 + 压缩 + 序列化
from agentorchestra.runtime.core.message import Message

manager = HistoryManager(min_retain_rounds=2)
for i in range(6):
    manager.append(Message(f"问{i}", "user"))
    manager.append(Message(f"答{i}", "assistant"))
print(manager.estimate_rounds())          # 6
manager.compress("前四轮已归档：用户咨询了 ABC 三件事")
history = manager.get_history()           # summary + 最近 2 轮
data = manager.to_dict()                  # 序列化
restored = HistoryManager()
restored.load_from_dict(data)

# 2) Token 预算与增量
counter = TokenCounter()
total = counter.begin_session(history)          # 首轮全量
total = counter.append_and_count(Message("继续", "user"))  # 增量追加
print(total, counter.get_cache_stats()["cached_messages"])

# 3) 工具输出截断（默认保留头部）
truncator = ObservationTruncator(max_lines=10, max_bytes=5120,
                                 truncate_direction="head", output_dir="tmp-tool-output")
result = truncator.truncate("search", "第1行\n" + "\n".join(f"行{i}" for i in range(2, 200)))
print(result["truncated"], result["full_output_path"])   # True 且完整输出已写盘

# 4) 直接构建结构化上下文（离线、本地算 Token）
builder = ContextBuilder(config=ContextConfig(max_tokens=2000))
context = builder.build(
    user_query="帮我看看订单 o1 的状态",
    conversation_history=[Message("你好", "user"), Message("您好，请问有什么可以帮您", "assistant")],
    system_instructions="你是订单助手，回答要简洁。",
)
print(context[:120])
```

相关 Config 字段（`Config` 的扁平旧字段名与子配置均可写，`Agent` 启动装配时读取）：

| Config 字段（含旧扁平名） | 子配置路径 | 作用 | 默认 |
| --- | --- | --- | --- |
| `context_window` | `history.context_window` | 触发历史压缩的 Token 阈值基准 | `128000` |
| `compression_threshold` | `history.compression_threshold` | 压缩阈值比例 | `0.8` |
| `min_retain_rounds` | `history.min_retain_rounds` | 压缩后保留的完整轮数 | `10` |
| `max_history_length` | `history.max_history_length` | 历史长度上限（当前为预留字段） | `100` |
| `enable_smart_compression` | `smart_compression.enabled` | 是否用 LLM 生成结构化摘要（opt-in） | `False` |
| `summary_llm_provider/model` | `smart_compression.*` | 摘要专用轻量模型 | `deepseek/deepseek-chat` |
| `context_builder_enabled` | `context_builder.enabled` | 是否启用 GSSC 构建器（opt-in） | `False` |
| `context_builder_max_tokens` | `context_builder.max_tokens` | GSSC 预算 | `8000` |
| `tool_output_max_lines/max_bytes` | `tool_output.*` | 截断行/字节上限 | `2000 / 51200` |
| `tool_output_truncate_direction` | `tool_output.truncate_direction` | 截断方向 `head/tail/head_tail` | `head` |
| `tool_output_dir` | `tool_output.output_dir` | 完整输出归档目录 | `tool-output` |

注意事项：
- 构造任意 `Agent`（即使不用上下文工程 API）也会创建 `truncator`，并 `os.makedirs(output_dir)` 产生 `tool-output/` 目录，属预期副作用，可用 `config.tool_output.output_dir` 改到临时目录。
- `Agent.add_message` 会走 `_should_compress()`（用增量计数缓存判断）自动触发压缩；`enable_smart_compression=True` 时会额外产生一次摘要 LLM 调用，注意隐式成本。
- 角色过滤：发送给 OpenAI 系模型前，非 `user/assistant/system/tool` 的角色会被跳过，`summary` 会转成带前缀的 system 文本（见 `SimpleAgent._build_messages`）。

## 与其他模块的关系

- 依赖：`runtime.context` 各文件只依赖 `runtime.core.message.Message`（历史/计数/构建均操作它）与 `runtime.core.utils.measure_elapsed_ms`（截断计时）；可选第三方 `tiktoken`。不依赖工具系统或 Agent 范式。
- 被依赖：
  - `runtime.core.agent.base.Agent.__init__` 在运行期导入 `HistoryManager / ObservationTruncator / TokenCounter`（`runtime.context.history / truncator / token_counter`）并装配为 `self.history_manager / self.truncator / self.token_counter`；`Agent._build_context` 在 `config.context_builder_enabled` 时使用 `ContextBuilder`（`base.py`）。
  - `runtime.agents.*` 各范式（如 `SimpleAgent._build_messages`、`ReActAgent._execute_tools_async`、`LoopAgent._run_tools_sync`）经 `Agent` 基类间接消费这些能力：历史注入、`summary` 摘要过滤、工具结果 `truncator.truncate` 截断、Token 增量计数。
  - `runtime.capabilities.builtins.ContextBuilderCapability` 在 `config.context_builder.enabled` 时安装同一个 `ContextBuilder` 到 `ctx.state["context_builder"]`。
- 循环依赖规避：`runtime.core` 对 `runtime.context` 的导入发生在 `Agent.__init__` 方法体内（延迟导入），而 `runtime.context` 只在模块顶层导入 `runtime.core.message/utils` 这种叶子模块，因此两者不会形成导入环。

## 测试

context 暂无独立测试文件，其行为经 Agent 与 Message 测试间接覆盖；直接相关测试：

```bash
python -m pytest tests/unit/test_agents.py -v        # Agent 生命周期内建 history/truncator/token 装配
python -m pytest tests/unit/test_core.py -v          # Message 序列化（context 的数据底座）
python -m pytest tests/unit -v                       # 全部单元测试
python examples/agent_full_demo.py                   # 端到端示例脚本（含 History/TokenCounter/Truncator 段）
```

新增针对本模块的用例建议放在 `tests/unit/test_context.py`（当前仓库尚无该文件，可新建）。
