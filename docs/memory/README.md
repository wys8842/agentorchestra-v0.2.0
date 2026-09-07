# Memory 模块

> 跨会话持久记忆：把"用户偏好 / 事实 / 经历 / 方法"类型化地写入存储、按关键词+向量召回，并提供去重、衰减、命名空间隔离与两个现成 Agent 工具。规范路径 `agentorchestra.capability.memory.*`，经典路径 `agentorchestra.memory.*` 依然可用。

## 设计动机与原则

1. **记忆是类型化的，不是一团文本**：`MemoryType` 区分 `fact/preference/episode/procedure`，不同类型有不同的写入与召回策略，也为摘要（Summarizer）产出提供结构化目标。
2. **存储可插拔，业务不感知**：`BaseMemoryBackend` 定义统一接口，`InMemoryBackend / JsonlBackend / SqliteBackend` 各自实现，`MemoryStore` 再包一层供上层使用（manager 只依赖 `MemoryStore`）。
3. **Embedding 是可选项，不是硬依赖**：`Embedder` 复用 LLM 的 OpenAI 兼容 `/embeddings` 接口，带 sha256 文本缓存；LLM 缺失、请求失败、被禁用时抛出 `EmbeddingUnavailable`，检索自动降级为纯关键词——离线也能跑。
4. **无外部依赖的检索**：分词只用正则（中英混合、每汉字切分），倒排索引纯内存构建；向量精排是余弦相似度，融合用 min-max 归一化（`alpha * keyword + (1-alpha) * cos`）。
5. **去重与遗忘都围绕时间**：写入前若与同 namespace 同类型已有条目相似度 ≥ `dedup_threshold` 则合并（保留旧 id、重要性取 max）而非新增；`recall` 命中会 `touch()` 重置 `updated_at`，可选 Ebbinghaus 衰减打分让旧记忆自然沉底。
6. **隔离内建**：每条记忆带 `namespace`；`_resolve_namespace` 在存在租户上下文时自动加租户前缀，实现多租户/多 Agent 记忆隔离。
7. **Agent 侧零改造**：记忆以 `MemorySaveTool / MemoryRecallTool` 两个标准工具暴露，注册进 `ToolRegistry` 即可；Agent 运行时可自动 recall（注入 system prompt 前缀）与自动总结（Summarizer → `remember_batch`）。
8. **失败不阻断主流程**：Embedding 失败、Summarizer 解析失败都返回空/跳过，绝不抛到 Agent run 主流程。

## 设计优势

- 记忆系统能叠加在任意 Agent 上，只需要一个 `MemoryManager` 实例 + 可选工具注册。
- 从"只有 SQLite"到"进程内演示"只差一个后端参数，`memory` 后端让单测与示例零文件副作用。
- 没有 LLM / 网络也能演示完整召回链路（关键词检索），方便调试与 CI。
- 摘要、去重、衰减做成独立组件，可单独替换或关闭（全部 opt-in）。

## 模块构成

| 路径 / 子模块 | 职责 | 主要公开导出 |
|---|---|---|
| `capability/memory/__init__.py` | 聚合导出（精简面） | `MemoryEntry`、`MemoryType`、`MemoryManager` |
| `capability/memory/models.py` | 数据模型 | `MemoryType`（fact/preference/episode/procedure）、`MemoryEntry`、`now_iso()`、`gen_id()` |
| `capability/memory/storage.py` | 存储后端 + 上层包装 | `BaseMemoryBackend`、`InMemoryBackend`、`JsonlBackend`、`SqliteBackend`、`MemoryStore` |
| `capability/memory/embedder.py` | Embedding 封装 | `Embedder`、`EmbeddingUnavailable` |
| `capability/memory/index.py` | 关键词倒排 + 混合检索 | `KeywordIndex`、`HybridRetriever`、`compute_decay()`（另有模块内 `_cosine/_tokenize`） |
| `capability/memory/manager.py` | 统一入口 | `MemoryManager`（`from_config/remember/recall/...`） |
| `capability/memory/summarizer.py` | 会话 → 记忆候选 | `Summarizer`、`MemoryCandidate` |
| `capability/memory/tools.py` | Agent 工具形态 | `MemorySaveTool`、`MemoryRecallTool` |
| `capability/memory/tiered_memory.py` | 三级缓存辅助（独立） | `MemoryTier`、`LRUCache`、`TieredMemory`（该文件自带同名 `MemoryEntry`，与 models 版不同） |

注意：`tiered_memory.py` 是一套独立的"L1 进程内 LRU / L2 SQLite / L3 长驻"缓存实现，自带字段不同的 `MemoryEntry`，目前不参与 `MemoryManager` 链路，也未被包 `__init__` 导出，需要时按需 import。

## 功能清单

### 1. 数据模型（models.py）

- `MemoryType(str, Enum)`：`FACT="fact"`（持久事实）、`PREFERENCE="preference"`（用户偏好/约定）、`EPISODE="episode"`（做过的任务/事件）、`PROCEDURE="procedure"`（方法/流程经验，与 skills 互补）。
- `MemoryEntry`（dataclass）：`id`（uuid hex）、`type`、`content`、`tags`、`importance(0~1)`、`embedding`（不参与默认 `to_dict`，向量单独落库）、`source_session/source_agent`、`namespace`（默认 `"default"`）、`created_at/updated_at`、`access_count/last_accessed_at`。
  - `touch()`（更新 `updated_at`）、`accessed()`（计数+1）；
  - `to_dict(include_embedding=False)` / `from_dict(data, embedding=None)`：向量由调用方从 embedding 存储单独取出回填。

### 2. 存储后端（storage.py）

统一接口 `upsert / get / delete / all(namespace) / save_embedding / get_embedding / stats / close`。

- `InMemoryBackend`：两本 dict（entries + embeddings），适合测试与示例。
- `JsonlBackend(filepath="memory/memories.jsonl")`：正文 JSONL 追加 + 同目录 `*.emb.jsonl` 存向量；启动读全量建缓存；`delete` 重写文件；追加式、人类可读。
- `SqliteBackend(db_path="memory/memories.db")`：`PRAGMA journal_mode=WAL`，表 `memories` + `memory_embeddings`（向量以 float32 little-endian 二进制存 BLOB）；老库缺 `namespace` 列会自动 `ALTER TABLE` 迁移并建索引；`check_same_thread=False` + 锁支持多线程。
- `MemoryStore(backend)`：业务层薄封装，暴露 `upsert/get/delete/iter_all(namespace)/stats/close`。manager 只持有它。

### 3. Embedder（embedder.py）

- `Embedder(llm=None, enabled=True, cache_size=10000)`；`available` 为 `enabled and llm is not None`。
- `embed(text)` 单条、`embed_texts(texts)` 批量；调用方签名约定 `llm` 具备 `base_url/model/api_key` 属性，HTTP POST 到 `<base_url>/embeddings`（OpenAI 风格）。sha256 缓存命中直接返回，超容简单截断一半。
- 任何失败（无 LLM、缺配置、网络、非 JSON、向量数不符）统一抛 `EmbeddingUnavailable`；调用方捕获后降级。

### 4. 检索（index.py）

- `KeywordIndex`：内存倒排（token → doc_id → 次数），标签命中加权 `3.0`；`build(entries)` 全量重建、`update/delete` 增量维护、`search(query, top_n=200)` 返回 `(doc_id, score)` 降序。
- `HybridRetriever(store, keyword_index, embedder=None, alpha=0.3)`：
  1. 关键词预筛（≤200 候选）；
  2. 按 namespace + 类型过滤；
  3. Embedder 可用则对候选算查询向量余弦，min-max 归一化后 `alpha*kw + (1-alpha)*cos` 融合；不可用/失败自动退化为关键词分；
  4. `decay_enabled=True` 时乘 Ebbinghaus 衰减因子并乘 `importance`；
  5. 取 `top_k`，命中后 `entry.touch()` 并回写（强化：让衰减计时重启）。
- `compute_decay(updated_at_iso, importance, now=None, tau_min_days=7.0, tau_max_days=180.0)`：`τ = tau_min + (tau_max-tau_min)*importance`，返回 `2^(-Δt/τ)` ∈ [0,1]；参数异常/时间不可解析返回 1.0。

### 5. MemoryManager（manager.py）——统一入口

构造推荐 `MemoryManager.from_config(config, llm=None, default_namespace=None)`：

- 读 `config.memory_backend`（`sqlite|jsonl|memory`，默认 sqlite）、`memory_db_path`/`memory_jsonl_path`、`memory_embedding_enabled`、`memory_dedup_threshold`、`memory_namespace`、`memory_decay_enabled` 及 `memory_decay_tau_min_days/max_days`；`llm=None` 时 Embedder 自动禁用（纯关键词模式）。
- 也可以手工组装：`MemoryStore(后端)` + `Embedder` + `KeywordIndex`（先 `build(store.iter_all())`）+ `HybridRetriever`，直接构造 `MemoryManager(...)`。

公开方法：

- `remember(content, type=MemoryType.FACT, tags=[], importance=0.5, source_session="", source_agent="", namespace=None) -> str`：写入前做**同 namespace 同类型**的向量相似度去重（embedder 不可用则跳过），命中合并返回旧 id，否则新建。`content` 为空抛 `ValueError`。
- `remember_batch(candidates: List[Dict]) -> List[str]`：逐条容错写入（单条失败只告警），供自动总结使用。
- `recall(query, top_k=5, types=None, namespace=None) -> List[MemoryEntry]`：走 HybridRetriever。
- `list(types=None, limit=50, namespace=None)`：按 `updated_at` 倒序列出。
- `forget(entry_id) -> bool`：删除并同步清理倒排索引。
- `stats()`、`close()`。
- namespace 解析：无租户上下文时按显式/默认返回；有 `TenantManager.current()` 时自动前缀 `tenant.namespace:ns`（重复前缀不再叠加）——见 `_resolve_namespace`。

### 6. Summarizer（summarizer.py）

`Summarizer(llm, max_chars=6000)`，`async extract(input_text, history, result) -> List[MemoryCandidate]`：

- 用系统提示要求 LLM 严格返回 JSON 数组（每项 `type/content/tags/importance`），取最近 10 条历史。
- 解析容错：先直解，失败再正则抽首个 `[...]` 块；单条损坏跳过。
- 任何 LLM 失败/超时/无 llm → 返回 `[]`（不影响主流程）。
- `MemoryCandidate(type, content, tags, importance)`：无 id/source 的候选，交给 `manager.remember_batch` 补全落库。

### 7. Agent 工具（tools.py）

- `MemorySaveTool(manager)`：工具名 `memory_save`，参数 `content`（必填）/`type`/`tags`（逗号分隔）/`importance`/`namespace`，非只读。
- `MemoryRecallTool(manager)`：工具名 `memory_recall`，参数 `query`（必填）/`type`（单类型过滤）/`top_k`（默认 5，钳到 1~50）/`namespace`，只读。
- 两者都继承 tools 模块的 `Tool` 协议，`type` 非法值返回 `INVALID_PARAM`；可直接 `registry.register_tool(...)`。

## 使用说明

### import 路径

```python
# 规范路径
from agentorchestra.capability.memory import MemoryEntry, MemoryType, MemoryManager
from agentorchestra.capability.memory.storage import InMemoryBackend, SqliteBackend, MemoryStore
from agentorchestra.capability.memory.embedder import Embedder
from agentorchestra.capability.memory.tools import MemorySaveTool, MemoryRecallTool

# 经典扁平路径（同一模块对象）
from agentorchestra.memory import MemoryManager as M2
```

### 场景 1：纯配置 + 内存后端（离线可跑）

```python
from agentorchestra.capability.memory import MemoryManager, MemoryType
from agentorchestra.runtime.core.config import Config

config = Config()
config.memory.backend = "memory"          # sqlite|jsonl|memory
config.memory.embedding_enabled = False   # 无 LLM → 关键词检索

mgr = MemoryManager.from_config(config, llm=None)

eid = mgr.remember(
    content="用户偏好使用 Python 与 SQLite",
    type=MemoryType.PREFERENCE,
    tags=["语言", "Python"],
)
mgr.remember(content="订单模块使用 SQLite 存储", tags=["技术栈"])

for hit in mgr.recall("Python", top_k=5):
    print(hit.type.value, hit.content, hit.tags)

print(mgr.list(limit=10))                 # 按 updated_at 倒序
print(mgr.stats())
mgr.close()
```

### 场景 2：手工组装（不经过 Config）

```python
from agentorchestra.capability.memory import MemoryManager, MemoryType
from agentorchestra.capability.memory.storage import InMemoryBackend, MemoryStore
from agentorchestra.capability.memory.embedder import Embedder
from agentorchestra.capability.memory.index import KeywordIndex, HybridRetriever

store = MemoryStore(InMemoryBackend())
keyword_index = KeywordIndex()
keyword_index.build(store.iter_all())                       # 建空索引也行
retriever = HybridRetriever(store, keyword_index, embedder=Embedder(enabled=False))

mgr = MemoryManager(store=store, embedder=Embedder(enabled=False),
                    keyword_index=keyword_index, retriever=retriever,
                    dedup_threshold=0.92, decay_enabled=False)
```

### 场景 3：持久化到 SQLite 并注册 Agent 工具

```python
from agentorchestra.capability.memory import MemoryManager
from agentorchestra.capability.memory.storage import SqliteBackend, MemoryStore
from agentorchestra.capability.memory.embedder import Embedder
from agentorchestra.capability.memory.index import KeywordIndex, HybridRetriever
from agentorchestra.capability.memory.tools import MemorySaveTool, MemoryRecallTool
from agentorchestra.capability.tools import ToolRegistry

store = MemoryStore(SqliteBackend(db_path="memory/memories.db"))
keyword_index = KeywordIndex()
keyword_index.build(store.iter_all())
mgr = MemoryManager(store=store,
                    embedder=Embedder(enabled=False),          # 无 LLM：纯关键词
                    keyword_index=keyword_index,
                    retriever=HybridRetriever(store, keyword_index, embedder=None))

registry = ToolRegistry()
registry.register_tool(MemorySaveTool(mgr))
registry.register_tool(MemoryRecallTool(mgr))

resp = registry.execute_tool("memory_save", {
    "content": "用户来自华东区域", "type": "fact", "tags": "region",
})
print(resp.status.value, resp.data.get("entry_id"))
```

### 关键配置（Config legacy 扁平字段，映射到 `memory.*` 子配置）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `memory_enabled` | `False` | MemoryCapability 总开关（Agent 装配时启用） |
| `memory_backend` | `sqlite` | `sqlite` / `jsonl` / `memory` |
| `memory_db_path` | `memory/memories.db` | SQLite 路径 |
| `memory_jsonl_path` | `memory/memories.jsonl` | JSONL 路径（含 `*.emb.jsonl`） |
| `memory_namespace` | `default` | 默认命名空间 |
| `memory_embedding_enabled` | `True` | Embedder 开关（无 LLM 时自动不可用） |
| `memory_dedup_threshold` | `0.92` | 写入去重余弦阈值 |
| `memory_decay_enabled` | `False` | Ebbinghaus 衰减打分开关 |
| `memory_decay_tau_min_days` | `7.0` | importance=0 的半衰期（天） |
| `memory_decay_tau_max_days` | `180.0` | importance=1 的半衰期（天） |
| `memory_auto_register_tools` | `True` | 自动注册 memory_save/memory_recall |
| `memory_auto_recall` | `True` | run 前自动 recall 并注入 system prompt |
| `memory_auto_summarize` | `False` | run 后自动 Summarizer 落库 |
| `memory_recall_top_k` | `5` | 自动 recall 条数 |

## 与其他模块的关系

- **capability.tools**：`tools.py` 的工具继承 `tools.base.Tool`，用 `tools.response`/`tools.errors` 构造响应（错误码含 `INVALID_PARAM`/`EXECUTION_ERROR`）；Agent 通过 `ToolRegistry` 使用它们。
- **capability.skills**：`MemoryType.PROCEDURE` 存"方法经验"，文档语义上与 Skills 互补——技能是"按需加载的静态知识"，procedure 记忆是"经验沉淀"。
- **governance.tenancy.tenant**：`MemoryManager._resolve_namespace` 在存在 `TenantManager.current()` 时给 namespace 加租户前缀做隔离（M6 多租户）。
- **runtime.capabilities.builtins**：`MemoryCapability` 按配置装配：`MemoryManager.from_config(config, llm)` → 存入 `agent.memory_manager`，按需自动注册两个记忆工具。
- **runtime.core.agent.base**：Agent 在 `run` 前若 `memory_auto_recall` 开启会 `recall` 并把命中拼成 system prompt 前缀；`_auto_memorize` 用 `Summarizer(llm=self.llm)` 提炼并 `remember_batch`（仅 `memory_auto_summarize=True`）。
- **tools.builtin 同级持久化**：DevLogTool 落 `memory/devlogs/`、TodoWriteTool 落 `memory/todos/`，与记忆库共用 `memory/` 目录但互不干扰。

## 测试

```bash
# 仓库现状：tests/unit 下暂无 memory 专项单测文件。
# 可运行工具侧单测验证 Tool 协议可正常注册/执行：
python -m pytest tests/unit/test_tools.py -v

# 用上方“场景 1”示例做内存冒烟（零文件副作用）：
python -c "from agentorchestra.capability.memory import MemoryManager, MemoryType; from agentorchestra.runtime.core.config import Config; c=Config(); c.memory.backend='memory'; c.memory.embedding_enabled=False; m=MemoryManager.from_config(c); print(m.remember(content='x', type=MemoryType.FACT)); print([e.content for e in m.recall('x')])"
```

若需要落地专项覆盖，可参照 `tests/unit/test_tools.py` 的风格新建 `tests/unit/test_memory.py`（pytest.ini `asyncio_mode = auto`，测试中全部使用 `InMemoryBackend`）。
