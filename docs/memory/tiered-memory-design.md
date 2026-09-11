# TieredMemory 三级缓存引擎设计

- 日期：2026-09-11
- 状态：待评审
- 范围：`agentorchestra/capability/memory/tiered_memory.py`（独立组件；不接入 `MemoryManager`）

## 背景

`tiered_memory.py` 于提交 `fcf6d3e` 作为批量特性之一引入，无设计文档、无测试、无调用方。其 docstring 声称实现"L1 工作记忆 / L2 短期记忆 / L3 长期记忆"三级缓存，具备"自动晋升/降级、容量控制、跨级一致性"，但实际交付只有骨架：

- L1 是真实可用的内存 LRU（`LRUCache`）；
- L2/L3 只是注入的 duck-typed `get/put` 对象，无任何实现；
- 写入只落 L1，`LRUCache.put` 返回的被淘汰条目被 `TieredMemory.put` 静默丢弃 → 数据丢失；
- 只有"L1 命中 N 次 → 写 L3"半条晋升链，无回写、无降级、无一致性、无锁；
- `_record_access` 内再次 `l1.get()` 造成副作用重复计数。

本设计在不改变组件独立定位的前提下，补齐为可自洽运行的三级缓存引擎，忠于原始意图。

## 目标 / 非目标

**目标**

- 真实的淘汰级联：L1 淘汰回写 L2，L2 容量淘汰下沉 L3，任何层级都不再静默丢数据。
- 逐级读取 L1→L2→L3，命中上提 L1；访问计数达阈值将条沉淀 L3。
- 三层统一存储 `CacheEntry`，元数据（importance/access_count/时间）跨层保留。
- 单个 `RLock` 保证复合操作线程安全。
- 提供 `StorageAdapter` 协议与有界/无界内存实现；L2/L3 可注入自定义适配器。
- `flush()` 支持退出前强制级联落 L3。
- 默认构造即为可用的三层结构。

**非目标**

- 不接入 `MemoryManager` / `HybridRetriever`（保持独立）。
- 不内置 SQLite/文件持久化实现（留 Protocol 注入；可后续补）。
- 不引入后台线程 / 定时衰减（衰减如实现，采用显式 `prune()`）。
- 不实现 Ebbinghaus 衰减打分（`prune` 为可选 TTL，非本期核心）。

## 架构

```
        get / put / delete / flush / stats   (单个 RLock)
TieredMemory ───────────────────────────────────────────
   L1 热   LRUCache(OrderedDict, 有容量)         进程内 / 微秒
   L2 温   StorageAdapter 默认 InMemoryTierStorage(capacity=4096)
   L3 冷   StorageAdapter 默认 InMemoryTierStorage(capacity=0 不限)
```

三层均存储 `CacheEntry`（非裸 value），保证跨层移动时元数据不丢失。

## 数据模型

### `CacheEntry`（dataclass）

| 字段 | 说明 |
|---|---|
| `key: str` | 键 |
| `value: Any` | 值 |
| `tier: MemoryTier` | 当前所在层，随移动更新 |
| `created_at: float` | 创建时间（`time.time()`） |
| `last_accessed: float` | 最近访问时间 |
| `access_count: int` | 访问次数 |
| `importance: float` | 重要性 0~1 |

方法：`touch()`（更新 `last_accessed` 与 `access_count += 1`）、`to_dict()` / `from_dict()`（供持久化适配器序列化）。

**命名**：原 `MemoryEntry` 与 `models.MemoryEntry` 冲突，重命名为 `CacheEntry`，保留 `MemoryEntry = CacheEntry` 别名以兼容旧 import。

### `MemoryTier`

保持原枚举：`L1_WORKING` / `L2_SHORT` / `L3_LONG`。

## 接口

### `StorageAdapter`（`typing.Protocol`）

```python
def get(key: str) -> Optional[CacheEntry]: ...
def put(key: str, entry: CacheEntry) -> Optional[CacheEntry]: ...  # 有界实现返回被淘汰者
def delete(key: str) -> bool: ...
def keys() -> List[str]: ...
def __contains__(self, key: str) -> bool: ...
def __len__(self) -> int: ...
```

### `InMemoryTierStorage(capacity=0)`

`OrderedDict` 实现的内存适配器；`capacity <= 0` 表示不限量。有界时 `put` 驱逐最久未用并返回之。既作为 L2/L3 默认实现，也作为外部持久化适配器的参考实现。

### `LRUCache(capacity)`

保留，L1 专用，内部自带锁；`put` 返回被淘汰 `CacheEntry`。

### `TieredMemory`

```python
TieredMemory(
    l1_capacity=256,
    l2_storage=None,              # None → InMemoryTierStorage(capacity=4096)
    l3_storage=None,              # None → InMemoryTierStorage(capacity=0)
    promotion_threshold=3,
    importance_l3_threshold=0.8,  # 高重要性旁路直写 L3（保留原行为）
    write_through=False,          # True 时 put 同时落 L3
)
```

保留方法：`get` / `put` / `delete` / `stats` / `promote_to_l3`。
新增方法：`flush` / `clear` / 可选 `prune`。

## 读写状态机

### `get(key)`

1. L1 命中 → `touch` → `_maybe_promote_l3` → 返回 value
2. L1 未中、L2 命中 → `tier=L2` → `_insert_l1(entry)` → `touch` → `_maybe_promote_l3` → 返回
3. L1/L2 未中、L3 命中 → `tier=L3` → `_insert_l1(entry)` → `touch` → `_maybe_promote_l3` → 返回
4. 全未中 → `None`

### `put(key, value, importance=0.5, tier=L1_WORKING)`

```
entry = CacheEntry(...)
_insert_l1(entry)
if write_through or importance >= importance_l3_threshold:
    l3.put(key, entry)
```

### `_insert_l1(entry)`（淘汰级联，核心闭环）

```
evicted = l1.put(entry)
if evicted:
    _sink(evicted)          # 旧代码在此静默丢弃
```

### `_sink(entry)`

```
entry.tier = L2
evicted2 = l2.put(entry.key, entry)
if evicted2:
    evicted2.tier = L3
    l3.put(evicted2.key, evicted2)
```

### `_maybe_promote_l3(entry)`

```
count = _access_counts.get(key, 0) + 1
if count >= promotion_threshold:
    l3.put(key, entry)
    _access_counts[key] = 0
```

### `flush()`

**移动**语义（源层删除、目标层写入），保证数据落至 L3：

1. 遍历 L1 全部条目，逐个 `_sink` 至 L2（L2 溢出再级联 L3），并从 L1 删除；
2. 遍历 L2 全部条目，逐个写入 L3，并从 L2 删除。

### `delete(key)` / `clear()`

- `delete`：三层删除 + 清计数，返回任一层是否存在。
- `clear`：清空 L1 与计数；对 L2/L3 通过 `keys()` + `delete()` 逐条清除（协议不提供批量清空）。

### `stats()`

返回各层 size 及计数：`hits_l1/l2/l3`、`misses`、`promotions`、`demotions`、`evictions`、`writebacks`、`errors`。

## 线程安全与错误处理

- 单个 `self._lock = RLock()` 包裹全部复合操作；`LRUCache` 自带锁，`TieredMemory` 外层锁保证跨层操作原子；`_access_counts` 不再裸访问。
- 外部适配器异常**优雅降级**（符合仓库"失败不阻断主流程"风格）：
  - `get` 失败 → 视为未中，记 `errors`，记日志；
  - `put` 失败 → 记 `errors`，记日志，不抛出；
  - `delete` 失败 → 记 `errors`，返回 False。
- 不允许因缓存层故障影响调用方业务，但计数器可观测故障率。

## 兼容性

- 保留公开名与签名：`MemoryTier`、`LRUCache`、`TieredMemory`、`get/put/delete/stats/promote_to_l3`。
- 新增 `CacheEntry`（含 `MemoryEntry` 别名）、`StorageAdapter`、`InMemoryTierStorage`。
- 行为变化（预期修复）：L1 淘汰不再丢数据；`_record_access` 不再重复计数；默认构造创建三层；所有公开方法线程安全。

## 改动范围

| 文件 | 改动 |
|---|---|
| `agentorchestra/capability/memory/tiered_memory.py` | 重写扩展（约 +150 行） |
| `docs/memory/README.md` | 更新 35/37 行，移除"未闭环 / 不参与链路"表述 |
| `tests/unit/test_tiered_memory.py` | 新增单测 |
| `docs/memory/tiered-memory-design.md` | 本文件 |

## 测试计划（`tests/unit/test_tiered_memory.py`，`asyncio_mode=auto`，全部内存）

1. 默认构造为三层（L2/L3 默认存在）。
2. L1 淘汰回写 L2：容量 2 写入 3 条，第 1 条在 L2 可查。
3. L2 淘汰下沉 L3：小 L2 容量，溢出后条目在 L3。
4. 逐级读命中上提：L2/L3 命中后出现在 L1。
5. 访问阈值沉淀 L3。
6. `write_through=True` 时 `put` 立即落 L3。
7. `importance >= threshold` 旁路直写 L3。
8. `flush()` 后 L3 含全部数据。
9. 元数据跨层保留：importance/access_count 在移动后不变（除访问自增）。
10. 计数正确：hits/misses/promotions/demotions/evictions。
11. 并发 get/put 不抛异常且总量守恒（`threading` 多线程冒烟）。
12. 适配器异常时优雅降级（注入会抛异常的 fake adapter，`get` 返回 None、`put` 不抛且 `errors` 增加）。
13. 兼容：`MemoryEntry is CacheEntry`；`promote_to_l3` 仍可用。

## 验证命令

```bash
python -m pytest tests/unit/test_tiered_memory.py -v
```
