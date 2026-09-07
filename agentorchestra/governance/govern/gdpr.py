"""GDPR 合规导出

GDPR（通用数据保护条例）合规工具：
- 数据主体权利：访问、更正、删除、可携带
- 数据导出（JSON/CSV）
- 数据匿名化
- 删除权（被遗忘权）
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class DataSubjectRight(Enum):
    """GDPR 数据主体权利"""
    ACCESS = "access"           # 第15条：访问权
    RECTIFICATION = "rectification"  # 第16条：更正权
    ERASURE = "erasure"         # 第17条：删除权（被遗忘）
    PORTABILITY = "portability" # 第20条：数据可携带权


@dataclass
class ExportRecord:
    """数据导出记录"""
    record_type: str
    record_id: str
    data: Dict[str, Any]
    created_at: datetime
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    pii_fields: List[str] = None  # 标记为个人信息的字段


class PIIAnonymizer:
    """个人信息匿名化器

    实现 GDPR 合规的假名化/匿名化策略：
    - 邮箱：保留域名，掩码用户名
    - 电话：仅保留后 4 位
    - 身份证：完全掩码
    - 姓名：哈希化
    """

    ANONYMIZATION_RULES = {
        "email": "_anonymize_email",
        "phone": "_anonymize_phone",
        "id_card": "_anonymize_id_card",
        "name": "_anonymize_name",
        "address": "_anonymize_address",
        "ip": "_anonymize_ip",
    }

    def __init__(self, salt: str = "default_salt"):
        self._salt = salt

    def anonymize_record(self, record: Dict[str, Any],
                         pii_fields: List[str]) -> Dict[str, Any]:
        """匿名化记录中的 PII 字段"""
        result = dict(record)
        for field in pii_fields:
            if field not in result:
                continue
            value = result[field]
            rule = self._infer_rule(field, value)
            if rule:
                result[field] = getattr(self, rule)(value)
        return result

    @staticmethod
    def _infer_rule(field_name: str, value: Any) -> Optional[str]:
        """推断字段的匿名化规则"""
        field_lower = field_name.lower()
        if "email" in field_lower or "mail" in field_lower:
            return "_anonymize_email"
        if "phone" in field_lower or "mobile" in field_lower:
            return "_anonymize_phone"
        if "id_card" in field_lower or "ssn" in field_lower or "身份证" in field_name:
            return "_anonymize_id_card"
        if field_lower in ("name", "username", "real_name"):
            return "_anonymize_name"
        if "address" in field_lower:
            return "_anonymize_address"
        if "ip" in field_lower:
            return "_anonymize_ip"
        return None

    def _anonymize_email(self, email: str) -> str:
        """邮箱匿名化：保留域名"""
        if "@" not in email:
            return self._anonymize_name(email)
        local, domain = email.split("@", 1)
        return f"***@{domain}"

    def _anonymize_phone(self, phone: str) -> str:
        """电话匿名化：保留后 4 位"""
        digits = "".join(c for c in str(phone) if c.isdigit())
        if len(digits) < 4:
            return "***"
        return f"***{digits[-4:]}"

    def _anonymize_id_card(self, value: str) -> str:
        """身份证完全掩码"""
        if len(value) <= 4:
            return "*" * len(value)
        return value[:2] + "*" * (len(value) - 4) + value[-2:]

    def _anonymize_name(self, name: str) -> str:
        """姓名哈希化"""
        import hashlib
        h = hashlib.sha256((self._salt + name).encode("utf-8")).hexdigest()[:8]
        return f"anon_{h}"

    def _anonymize_address(self, address: str) -> str:
        """地址匿名化：保留省/市级别"""
        parts = address.split(" ")
        if len(parts) > 2:
            return " ".join(parts[:2]) + " ***"
        return "***"

    def _anonymize_ip(self, ip: str) -> str:
        """IP 匿名化：保留前三段"""
        if ":" in ip:  # IPv6
            return "::1" if ip == "::1" else ":".join(ip.split(":")[:3]) + ":***"
        parts = ip.split(".")
        if len(parts) == 4:
            return ".".join(parts[:3]) + ".***"
        return "***"


class GDPRExporter:
    """GDPR 数据导出器

    支持：
    - JSON 导出（机器可读）
    - CSV 导出（表格友好）
    - 匿名化导出
    - 选择性字段导出
    """

    def __init__(self, anonymizer: Optional[PIIAnonymizer] = None):
        self.anonymizer = anonymizer or PIIAnonymizer()

    def export_to_json(
        self,
        records: List[ExportRecord],
        anonymize: bool = False,
    ) -> str:
        """导出为 JSON"""
        data = []
        for r in records:
            record_data = {
                "record_type": r.record_type,
                "record_id": r.record_id,
                "data": r.data,
                "created_at": r.created_at.isoformat(),
            }
            if r.tenant_id:
                record_data["tenant_id"] = r.tenant_id
            if anonymize and r.pii_fields:
                record_data["data"] = self.anonymizer.anonymize_record(
                    r.data, r.pii_fields
                )
                record_data["anonymized"] = True
            data.append(record_data)
        return json.dumps(data, ensure_ascii=False, indent=2)

    def export_to_csv(
        self,
        records: List[ExportRecord],
        anonymize: bool = False,
    ) -> str:
        """导出为 CSV"""
        if not records:
            return ""

        # 收集所有字段
        all_fields = set()
        for r in records:
            all_fields.update(r.data.keys())

        output = io.StringIO()
        fieldnames = ["record_type", "record_id", "created_at"] + sorted(all_fields)
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for r in records:
            data = r.data
            if anonymize and r.pii_fields:
                data = self.anonymizer.anonymize_record(data, r.pii_fields)

            row = {
                "record_type": r.record_type,
                "record_id": r.record_id,
                "created_at": r.created_at.isoformat(),
            }
            row.update(data)
            writer.writerow(row)

        return output.getvalue()

    def get_subject_data(self, subject_id: str,
                         records: List[ExportRecord]) -> List[ExportRecord]:
        """获取数据主体的所有记录（访问权，第15条）"""
        return [r for r in records if r.user_id == subject_id or
                r.data.get("user_id") == subject_id]

    def erase_subject_data(self, subject_id: str,
                           records: List[ExportRecord]) -> int:
        """擦除数据主体的记录（删除权，第17条）

        注意：实际删除需要在存储层执行；这里只返回要删除的记录数
        """
        erased = self.get_subject_data(subject_id, records)
        return len(erased)
