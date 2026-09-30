"""理货记录业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "tally"
REQUIRED_FIELDS = ["理货编号", "对应船舶", "箱量核对"]
STATUS_ORDER = ["待理货", "理货中", "已核对", "有溢短"]
ACTION_RULES = {"开始理货": "理货中", "提交核对": "已核对", "登记溢短": "有溢短"}
NEGATIVE_ACTIONS = []

# 理货批次：同一条船的箱位整组进入待核区，逐条核对后流转。
BATCH_MODULE = "tally_batch"
ITEM_PENDING = "待核"
ITEM_VERIFIED = "已核"
ITEM_EXCEPTION = "有溢短"
ITEM_STATUSES = [ITEM_PENDING, ITEM_VERIFIED, ITEM_EXCEPTION]
BATCH_STATUSES = ["待核中", "溢短待处理", "已核完成"]


class TallyService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("理货编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"理货记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于理货记录可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"理货记录已{action}"


def _parse_count(value: Any) -> int | None:
    """把箱量入参收成非负整数；收不下就返回 None 让调用方报错。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value or "").strip()
    if not text or not text.isdigit():
        return None
    return int(text)


class TallyBatchService:
    """理货批次：整组进待核区、逐条核对、溢短单列、打回重来。"""

    def _rows(self) -> list[dict[str, Any]]:
        return store.rows(BATCH_MODULE)

    def _find(self, batch_id: int) -> dict[str, Any] | None:
        return store.find(BATCH_MODULE, batch_id)

    def _refresh_flags(self, batch: dict[str, Any]) -> None:
        items = batch["条目"]
        batch["pending"] = any(item["状态"] == ITEM_PENDING for item in items)
        batch["abnormal"] = any(item["状态"] == ITEM_EXCEPTION for item in items)

    def _summary(self, batch: dict[str, Any]) -> dict[str, Any]:
        items = batch["条目"]
        pending = sum(1 for item in items if item["状态"] == ITEM_PENDING)
        verified = sum(1 for item in items if item["状态"] == ITEM_VERIFIED)
        exception = sum(1 for item in items if item["状态"] == ITEM_EXCEPTION)
        if pending:
            batch_status = "待核中"
        elif exception:
            batch_status = "溢短待处理"
        else:
            batch_status = "已核完成"
        return {
            "条目数": len(items),
            "待核数": pending,
            "已核数": verified,
            "溢短数": exception,
            "应核总量": sum(int(item["应核箱量"]) for item in items),
            "实核总量": sum(int(item["实核箱量"]) for item in items if item["实核箱量"] is not None),
            "批次状态": batch_status,
        }

    def _view(self, batch: dict[str, Any]) -> dict[str, Any]:
        """批次对外视图：带上汇总与自动挑出的溢短条目。"""
        view = {key: value for key, value in batch.items() if key != "条目"}
        view["条目"] = [dict(item) for item in batch["条目"]]
        view["汇总"] = self._summary(batch)
        view["溢短条目"] = [dict(item) for item in batch["条目"] if item["状态"] == ITEM_EXCEPTION]
        return view

    def list_batches(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._view(batch) for batch in self._rows()]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("批次号", "")) or keyword in str(row.get("对应船舶", ""))
            ]
        if status:
            rows = [row for row in rows if row["汇总"]["批次状态"] == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        batch = self._find(batch_id)
        return self._view(batch) if batch is not None else None

    def create_batch(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, bool]:
        """整组登记：同一条船的箱位多选后一次进待核区；批次号重复时只认第一次。"""
        batch_no = str(values.get("批次号") or "").strip()
        vessel = str(values.get("对应船舶") or "").strip()
        missing = [name for name, val in (("批次号", batch_no), ("对应船舶", vessel)) if not val]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}", False
        for batch in self._rows():
            if batch.get("批次号") == batch_no:
                return self._view(batch), f"批次 {batch_no} 已提交过，同一批重复提交只认第一次", True
        raw_items = values.get("条目")
        if not isinstance(raw_items, list) or not raw_items:
            return None, "请先勾选同一条船下的箱位，整组进入待核区", False
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                return None, "条目格式不正确，每条要包含箱位编号与应核箱量", False
            position = str(raw.get("箱位编号") or "").strip()
            if not position:
                return None, "条目里存在空的箱位编号，请检查勾选结果", False
            if position in seen:
                return None, f"箱位 {position} 在同一批里重复勾选", False
            planned = _parse_count(raw.get("应核箱量"))
            if planned is None:
                return None, f"箱位 {position} 的应核箱量要填非负整数", False
            seen.add(position)
            items.append({
                "箱位编号": position,
                "应核箱量": planned,
                "实核箱量": None,
                "核对结果": "",
                "溢短量": 0,
                "状态": ITEM_PENDING,
            })
        rows = self._rows()
        batch = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "批次号": batch_no,
            "对应船舶": vessel,
            "理货人员": str(values.get("理货人员") or "").strip(),
            "残损情况": "",
            "登记时间": str(values.get("登记时间") or date.today().isoformat()),
            "条目": items,
        }
        self._refresh_flags(batch)
        rows.append(batch)
        return self._view(batch), f"批次 {batch_no} 已登记，{len(items)} 个箱位整组进入待核区", False

    def submit_verify(self, batch_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """提交核对：残损统一录、箱量逐条对，一致的转已核，溢短的自动单列。"""
        batch = self._find(batch_id)
        if batch is None:
            return None, f"理货批次 {batch_id} 不存在或已归档"
        raw_details = values.get("明细")
        if not isinstance(raw_details, list) or not raw_details:
            return None, "核对明细不能为空，箱量核对结果要逐条填写"
        actuals: dict[str, int] = {}
        for raw in raw_details:
            if not isinstance(raw, dict):
                return None, "核对明细格式不正确，每条要包含箱位编号与实核箱量"
            position = str(raw.get("箱位编号") or "").strip()
            actual = _parse_count(raw.get("实核箱量"))
            if not position or actual is None:
                return None, f"箱位 {position or '（空）'} 的实核箱量要填非负整数"
            if position in actuals:
                return None, f"箱位 {position} 在核对明细里重复提交"
            actuals[position] = actual
        items = batch["条目"]
        by_position = {item["箱位编号"]: item for item in items}
        unknown = [pos for pos in actuals if pos not in by_position]
        if unknown:
            return None, f"箱位 {'、'.join(unknown)} 不在本批次里，请核对后再提交"
        blocked = [pos for pos in actuals if by_position[pos]["状态"] == ITEM_EXCEPTION]
        if blocked:
            return None, f"箱位 {'、'.join(blocked)} 有溢短待处理，请先打回再重新核对"
        pending_items = [item for item in items if item["状态"] == ITEM_PENDING]
        if not pending_items:
            return self._view(batch), "本批次没有待核条目，重复提交只认第一次结果"
        damage = str(values.get("残损情况") or "").strip()
        if not damage:
            return None, "残损情况未填写，空着不允许转入已核；无残损请填「无残损」"
        missing = [item["箱位编号"] for item in pending_items if item["箱位编号"] not in actuals]
        if missing:
            return None, f"箱位 {'、'.join(missing)} 还没填实核箱量，核对结果要逐条填齐"
        skipped = [pos for pos in actuals if by_position[pos]["状态"] == ITEM_VERIFIED]
        summary = _parse_count(values.get("汇总箱量"))
        if summary is None:
            return None, "核对汇总箱量要填非负整数"
        effective = sum(int(item["实核箱量"]) for item in items if item["状态"] == ITEM_VERIFIED)
        effective += sum(actuals[item["箱位编号"]] for item in pending_items)
        if summary != effective:
            return None, f"核对汇总箱量 {summary} 与核对明细合计 {effective} 对不上，请核对后再提交"
        batch["残损情况"] = damage
        verified = 0
        overflow = 0
        for item in pending_items:
            actual = actuals[item["箱位编号"]]
            planned = int(item["应核箱量"])
            item["实核箱量"] = actual
            if actual == planned:
                item["状态"] = ITEM_VERIFIED
                item["核对结果"] = "一致"
                item["溢短量"] = 0
                verified += 1
            elif actual > planned:
                item["状态"] = ITEM_EXCEPTION
                item["核对结果"] = "溢"
                item["溢短量"] = actual - planned
                overflow += 1
            else:
                item["状态"] = ITEM_EXCEPTION
                item["核对结果"] = "短"
                item["溢短量"] = planned - actual
                overflow += 1
        self._refresh_flags(batch)
        parts = [f"{verified} 条核对一致转入已核"]
        if overflow:
            parts.append(f"{overflow} 条有溢短已自动单列")
        if skipped:
            parts.append(f"箱位 {'、'.join(skipped)} 已核完，按首次结果保留")
        return self._view(batch), "；".join(parts)

    def reject_items(self, batch_id: int, positions: list[Any]) -> tuple[dict[str, Any] | None, str]:
        """打回：指定条目退回待核区等重来，已核完的不动。"""
        batch = self._find(batch_id)
        if batch is None:
            return None, f"理货批次 {batch_id} 不存在或已归档"
        wanted = [str(pos or "").strip() for pos in positions if str(pos or "").strip()]
        if not wanted:
            return None, "请先勾选要打回的箱位"
        by_position = {item["箱位编号"]: item for item in batch["条目"]}
        unknown = [pos for pos in wanted if pos not in by_position]
        if unknown:
            return None, f"箱位 {'、'.join(unknown)} 不在本批次里，无法打回"
        rejected: list[str] = []
        kept: list[str] = []
        for pos in wanted:
            item = by_position[pos]
            if item["状态"] == ITEM_VERIFIED:
                kept.append(pos)
                continue
            item["状态"] = ITEM_PENDING
            item["实核箱量"] = None
            item["核对结果"] = ""
            item["溢短量"] = 0
            rejected.append(pos)
        self._refresh_flags(batch)
        if not rejected:
            return None, f"箱位 {'、'.join(kept)} 已核完，已核的不动，没有可打回的条目"
        parts = [f"已打回 {len(rejected)} 条（{'、'.join(rejected)}）回待核区等重来"]
        if kept:
            parts.append(f"箱位 {'、'.join(kept)} 已核完保持不动")
        return self._view(batch), "；".join(parts)
