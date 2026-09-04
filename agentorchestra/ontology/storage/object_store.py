"""ObjectStore - 对象存储

组合对象索引（ObjectIndex）和关系图（GraphStore）：
- 对象写入带类型校验（复用 ObjectType.validate_object）
- 索引支持搜索/过滤/聚合
- 图支持链接查询/路径遍历
"""

from typing import Any, Dict, List, Optional

from ..semantic.object_type import ObjectType
from .backends import BaseStorageBackend, MemoryBackend
from .graph_store import GraphStore
from .index import ObjectIndex


class ObjectStore:
    """对象存储"""

    def __init__(self, graph: Optional[GraphStore] = None,
                 backend: Optional[BaseStorageBackend] = None,
                 materializer: Optional[Any] = None,
                 wal_hook: Optional[Any] = None,
                 enable_object_identity: bool = True):
        """初始化对象存储

        Args:
            graph: 图存储（关系/路径查询）
            backend: 存储后端（默认内存；传 SQLiteBackend 实现持久化）
            materializer: 物化管理器（可选，写操作后触发物化回写）
            wal_hook: WAL hook（可选，M0+），签名 wal_hook(thread_id, action_type, payload)
                所有 insert/update/delete 操作会调用 hook。失败不阻断主流程。
            enable_object_identity: 是否自动注入 version/created_tx/last_modified_tx（M3）。
        """
        self.index = ObjectIndex(backend=backend or MemoryBackend())
        self.graph = graph or GraphStore()
        self.materializer = materializer
        self.wal_hook = wal_hook  # type: ignore[assignment]
        self._types: Dict[str, ObjectType] = {}
        # 当前 thread 上下文（WAL emit 用）。Agent 在每步 checkpoint 前设置
        self._wal_thread_id: Optional[str] = None
        # 同步 WAL queue（collect-and-flush 模式，Agent 周期调 drain_wal）
        import threading as _threading
        self._wal_lock = _threading.Lock()
        self._wal_queue: List[Dict[str, Any]] = []
        # M3：对象身份（version 自动注入）+ 审计
        self.enable_object_identity = enable_object_identity
        self.audit: Optional[Any] = None  # AuditManager（可选）
        self.audit_backend: Optional[Any] = None  # 持久化 backend（CheckpointStore）
        self._tx_context: Optional[str] = None  # 当前 tx_id（identity 用）

    def configure_governance(self, audit: Optional[Any] = None,
                             audit_backend: Optional[Any] = None) -> None:
        """装配 M3 治理：审计管理器 + 持久化 backend。"""
        self.audit = audit
        self.audit_backend = audit_backend

    def set_tx_context(self, tx_id: Optional[str]) -> None:
        """设置当前事务上下文（created_tx/last_modified_tx 用）。"""
        self._tx_context = tx_id

    # ==================== 治理辅助（M3） ====================

    def _current_principal(self) -> str:
        """读取当前 principal（ContextVar 优先；兜底 anonymous）。"""
        try:
            from agentorchestra.governance.govern.identity import current_principal
            return current_principal()
        except Exception:
            return "anonymous"

    def _audit_write(self, action: str, type_name: str, pk: str,
                     success: bool = True, detail: Optional[Dict] = None) -> None:
        """写审计（若装配；失败不阻断）。"""
        if self.audit is None:
            return
        try:
            self.audit.log(
                principal=self._current_principal(),
                resource=type_name,
                action=action,
                detail={**(detail or {}), "obj_id": pk},
                success=success,
            )
        except Exception:
            pass

    def set_wal_thread_id(self, thread_id: Optional[str]) -> None:
        """设置当前 thread 上下文（M0 WAL 标签用）。

        Agent 在每步保存 checkpoint 前调用，所有后续写操作的 WAL entry 都带该 thread_id。
        设为 None 时关闭 WAL emit。
        """
        self._wal_thread_id = thread_id

    def drain_wal(self) -> List[Dict[str, Any]]:
        """取出并清空当前积压的 WAL 条目（同步）。

        Agent 在保存 checkpoint 前调用，把积压的条目批量写给 CheckpointStore。
        """
        with self._wal_lock:
            entries = self._wal_queue
            self._wal_queue = []
            return entries

    def pending_wal_count(self) -> int:
        """查看当前积压 WAL 条目数（不取出）。"""
        with self._wal_lock:
            return len(self._wal_queue)

    @property
    def backend_type(self) -> str:
        """当前存储后端类型"""
        return self.index.backend_type

    def close(self) -> None:
        """关闭存储后端"""
        self.index.close()

    # ==================== 类型注册 ====================

    def register_type(self, object_type: ObjectType) -> None:
        """注册对象类型（类型表 + 索引同步）"""
        self._types[object_type.api_name] = object_type
        self.index.register_type(object_type.api_name)

    def get_type(self, api_name: str) -> Optional[ObjectType]:
        """按 api_name 获取对象类型定义"""
        return self._types.get(api_name)

    def list_types(self) -> List[str]:
        """列出已注册对象类型名"""
        return list(self._types.keys())

    def _materialize(self, operation: str, type_name: str,
                     obj: Dict[str, Any], patch: Optional[Dict] = None) -> None:
        """触发物化回写（若配置了 materializer，失败不阻断主流程）"""
        if self.materializer is None:
            return
        try:
            self.materializer.materialize(operation, type_name, obj, patch)
        except Exception:
            pass

    def _wal_emit(self, action_type: str,
                  payload: Dict[str, Any]) -> None:
        """触发 WAL hook（M0+ 持久化）。

        Args:
            action_type: 'state_update' / 'link_create' / 'link_delete'
            payload: 任意 JSON 可序列化数据
        """
        if self._wal_thread_id is None:
            return
        # collect-and-flush：把 entry 推到内部 queue，由 Agent 在 checkpoint 时统一刷入 CheckpointStore
        entry = {
            "thread_id": self._wal_thread_id,
            "action_type": action_type,
            "payload": payload,
        }
        with self._wal_lock:
            self._wal_queue.append(entry)
        # 兼容老式 hook（如果外部传入）：仍然调用
        if self.wal_hook is not None:
            try:
                self.wal_hook(self._wal_thread_id, action_type, payload)
            except Exception:
                pass

    # ==================== 对象写入 ====================

    def insert(self, type_name: str, obj: Dict[str, Any]) -> Dict[str, Any]:
        """插入对象（校验主键/必填/类型/派生属性）"""
        obj_type = self._require_type(type_name)

        errors = obj_type.validate_object(obj)
        if errors:
            raise ValueError(f"对象校验失败: {errors}")

        # 拒绝写入派生属性（值由 Function 计算）
        derived_written = [p for p in obj if obj_type.is_derived(p)]
        if derived_written:
            raise ValueError(f"派生属性不可直接写入: {derived_written}")

        pk = str(obj[obj_type.primary_key])

        # 补充默认值
        for p in obj_type.get_properties():
            if p.name not in obj and p.default is not None:
                obj[p.name] = p.default

        # M3：注入对象身份（version/created_tx/last_modified_tx）
        if self.enable_object_identity:
            tx = self._tx_context or "none"
            obj["version"] = 1
            obj["created_tx"] = tx
            obj["last_modified_tx"] = tx

        self.index.index_object(type_name, pk, obj)

        # 同步图节点（显式 name，避免与业务字段 name 冲突）
        self.graph.merge_node(
            obj_type.api_name, dict(obj), name=f"{type_name}:{pk}")

        # WAL emit（M0+）
        self._wal_emit("state_update", {
            "op": "insert",
            "type": type_name,
            "pk": pk,
            "obj": dict(obj),
        })

        # 物化回写（可选）
        self._materialize("insert", type_name, dict(obj))

        # 审计（M3）
        self._audit_write("insert", type_name, pk)

        result = self.index.get(type_name, pk)
        assert result is not None
        return result

    def update(self, type_name: str, pk: str, patch: Dict[str, Any],
               expected_version: Optional[int] = None) -> Dict[str, Any]:
        """更新对象（部分字段，合并后重新校验）

        Args:
            expected_version: M3 CAS 校验：调用方读取对象时的 version。
                提交时版本不一致 → 抛 TxConflict。None = 不校验（向后兼容）。
        """
        obj_type = self._require_type(type_name)
        pk = str(pk)

        if obj_type.primary_key in patch:
            raise ValueError(f"主键 {obj_type.primary_key} 不可更新")

        # 拒绝写入派生属性
        derived_written = [p for p in patch if obj_type.is_derived(p)]
        if derived_written:
            raise ValueError(f"派生属性不可直接写入: {derived_written}")

        current = self.index.get(type_name, pk)
        if not current:
            raise ValueError(f"对象不存在: {type_name}/{pk}")

        # M3：CAS 校验（版本不一致 → TxConflict）
        if expected_version is not None:
            from agentorchestra.governance.tx.context import TxConflict
            cur_ver = int(current.get("version", 0) or 0)
            if cur_ver != expected_version:
                raise TxConflict(
                    f"CAS 冲突: {type_name}/{pk} 当前 version={cur_ver}，"
                    f"期望 {expected_version}"
                )

        merged = dict(current)
        merged.update(patch)

        # 合并后重新校验（未知属性/类型/必填）
        errors = obj_type.validate_object(merged)
        if errors:
            raise ValueError(f"对象校验失败: {errors}")

        # M3：version 递增 + last_modified_tx
        if self.enable_object_identity:
            merged["version"] = int(merged.get("version", 0) or 0) + 1
            merged["last_modified_tx"] = self._tx_context or merged.get(
                "last_modified_tx", "none")
            # patch 内若有系统字段由引擎覆盖（拒绝用户伪造 version）
            patch = {k: v for k, v in patch.items()
                     if k not in ObjectType.SYSTEM_FIELDS}

        self.index.update_object(type_name, pk, merged)
        self.graph.merge_node(
            obj_type.api_name, dict(merged), name=f"{type_name}:{pk}")

        # WAL emit（M0+）
        self._wal_emit("state_update", {
            "op": "update",
            "type": type_name,
            "pk": pk,
            "patch": dict(patch),
            "before": current,
            "after": merged,
        })

        # 物化回写（可选）
        self._materialize("update", type_name, dict(merged), patch=dict(patch))

        # 审计（M3）
        self._audit_write("update", type_name, pk, detail={
            "version": merged.get("version"),
            "patch_keys": list(patch.keys()),
        })
        return merged

    def delete(self, type_name: str, pk: str) -> bool:
        """删除对象（索引/图/审计联动），返回是否删除成功"""
        pk = str(pk)
        removed = self.index.remove_object(type_name, pk)
        if removed:
            # 清理图节点（使用公共接口）
            self.graph.remove_node(f"{type_name}:{pk}")
            self.graph.remove_node(pk)
            # WAL emit（M0+）
            self._wal_emit("state_update", {
                "op": "delete",
                "type": type_name,
                "pk": pk,
                "removed": removed,
            })
            # 物化回写（可选）
            self._materialize("delete", type_name, {"pk": pk}, patch=dict(removed))
            # 审计（M3）
            self._audit_write("delete", type_name, pk, detail={
                "version": removed.get("version"),
            })
        return removed is not None

    # ==================== 对象读取 ====================

    def get(self, type_name: str, pk: str) -> Optional[Dict[str, Any]]:
        """读取对象，不存在返回 None"""
        return self.index.get(type_name, str(pk))

    def list_objects(self, type_name: str) -> List[Dict[str, Any]]:
        """列出类型下全部对象"""
        return self.index.list(type_name)

    def search(self, type_name: str, query: str,
               fields: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """全文搜索（包含匹配）"""
        return self.index.search(type_name, query, fields)

    def filter(self, type_name: str, conditions: Dict[str, Any],
               operators: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        """按条件过滤对象（支持比较操作符）"""
        return self.index.filter(type_name, conditions, operators)

    def aggregate(self, type_name: str, group_by: str, agg: str = "count",
                  agg_field: Optional[str] = None) -> Dict[str, Any]:
        """按字段分组聚合统计"""
        return self.index.aggregate(type_name, group_by, agg, agg_field)

    def count(self, type_name: str) -> int:
        """统计类型下对象数量"""
        return self.index.count(type_name)

    # ==================== 链接 ====================

    def create_link(self, from_type: str, from_pk: str, link_name: str,
                    to_type: str, to_pk: str) -> None:
        """创建对象链接"""
        from_pk, to_pk = str(from_pk), str(to_pk)

        if not self.get(from_type, from_pk):
            raise ValueError(f"源对象不存在: {from_type}/{from_pk}")
        if not self.get(to_type, to_pk):
            raise ValueError(f"目标对象不存在: {to_type}/{to_pk}")

        # 关系校验：链接类型必须存在，且两端类型匹配（含子类继承）
        self._validate_link(from_type, link_name, to_type)

        self.graph.add_relationship(
            f"{from_type}:{from_pk}", link_name.upper(), f"{to_type}:{to_pk}",
            {"source": "object_store", "confidence": 1.0})

    def _validate_link(self, from_type: str, link_name: str, to_type: str) -> None:
        """校验链接类型：存在性 + 两端类型匹配（domain/range，含子类继承）"""
        from_type_def = self._require_type(from_type)
        to_type_def = self._require_type(to_type)

        link = from_type_def.get_link_type(link_name)
        if not link:
            raise ValueError(
                f"链接 '{link_name}' 未在对象类型 '{from_type}' 中定义")

        # from 端校验
        if link.from_type != from_type:
            # 允许子类（from_type 是 link.from_type 的子类）
            if not (from_type_def.is_subclass_of(link.from_type, self._types) or
                    link.from_type == from_type):
                raise ValueError(
                    f"链接 '{link_name}' 的源类型应为 {link.from_type}，实际 {from_type}")

        # to 端校验
        if link.to_type != to_type:
            if not (to_type_def.is_subclass_of(link.to_type, self._types) or
                    link.to_type == to_type):
                raise ValueError(
                    f"链接 '{link_name}' 的目标类型应为 {link.to_type}，实际 {to_type}")

    def get_subclasses(self, type_name: str, transitive: bool = True) -> List[str]:
        """获取对象类型的子类型（类层次查询）"""
        direct = [t for t, d in self._types.items()
                  if d.parent_type == type_name]
        result = list(direct)
        if transitive:
            for sub in direct:
                for deeper in self.get_subclasses(sub, transitive=True):
                    if deeper not in result:
                        result.append(deeper)
        return result

    def get_superclasses(self, type_name: str) -> List[str]:
        """获取对象类型的父类型链（含多级）"""
        chain = []
        current = self._types.get(type_name)
        visited = set()
        while current and current.parent_type:
            if current.parent_type in visited:
                break
            visited.add(current.parent_type)
            chain.append(current.parent_type)
            current = self._types.get(current.parent_type)
        return chain

    def get_links(self, from_type: str, from_pk: str,
                  link_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """查询对象链接"""
        from_pk = str(from_pk)
        related = self.graph.get_related(f"{from_type}:{from_pk}",
                                         link_name.upper() if link_name else None)
        results = []
        for r in related:
            to_type, _, to_pk = r["name"].partition(":")
            results.append({
                "to_type": to_type,
                "to_pk": to_pk,
                "link_name": r["rel"].lower(),
                "object": self.get(to_type, to_pk),
            })
        return results

    def query_links(self, from_type: str, from_pk: str, link_name: str,
                    max_depth: int = 3) -> List[Dict[str, Any]]:
        """跨链接路径查询（传递推理）"""
        paths = self.graph.query_paths(
            f"{from_type}:{from_pk}", link_name.upper(), max_depth)
        results = []
        for p in paths:
            to_type, _, to_pk = p["name"].partition(":")
            results.append({
                "to_type": to_type,
                "to_pk": to_pk,
                "depth": p["depth"],
                "path": p["path"],
                "object": self.get(to_type, to_pk),
            })
        return results

    # ==================== 工具 ====================

    def _require_type(self, type_name: str) -> ObjectType:
        obj_type = self._types.get(type_name)
        if not obj_type:
            raise ValueError(f"对象类型不存在: {type_name}")
        return obj_type

    def stats(self) -> Dict[str, Any]:
        """返回对象存储统计信息"""
        return {
            "types": len(self._types),
            "objects": {t: self.index.count(t) for t in self._types},
            "nodes": self.graph.node_count(),
            "edges": self.graph.edge_count(),
        }
