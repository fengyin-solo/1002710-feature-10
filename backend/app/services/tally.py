"""理货批量核对业务规则。

工作流：同船多个箱位整组建批进入「待核区」→ 逐条填报箱量、统一登记残损 →
系统按 实收箱量=单据箱量 自动挑出溢短条目单列 → 核对一致且残损已录的条目
才能转入「已核」→ 打回只影响被选中的已核条目，其余已核条目不动。

防重复：提交类动作携带 clientToken，同一批条目重复提交只认第一次的结果。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "tally"
SLOT_MODULE = "tally_slot"
BATCH_MODULE = "tally_batch"
REQUIRED_FIELDS = ["理货编号", "对应船舶", "箱量核对"]
STATUS_ORDER = ["待理货", "理货中", "已核对", "有溢短"]
ACTION_RULES = {"开始理货": "理货中", "提交核对": "已核对", "登记溢短": "有溢短"}
NEGATIVE_ACTIONS = []

ENTRY_PENDING = "待核"
ENTRY_VERIFIED = "已核"
ENTRY_REJECTED = "打回"

BATCH_REVIEWING = "待核中"
BATCH_DONE = "已核完"

DAMAGE_NONE = "无残损"


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _difference(entry: dict[str, Any]) -> int | None:
    """溢短=实收-单据；实收还没填时返回 None。"""
    actual = entry.get("actual_qty")
    if actual in (None, ""):
        return None
    try:
        return int(actual) - int(entry.get("expected_qty", 0))
    except (TypeError, ValueError):
        return None


class IdempotencyStore:
    """提交幂等记录：同一批次、同一动作、同一 token 只处理第一次。"""

    def __init__(self) -> None:
        self._seen: dict[tuple[int, str, str], dict[str, Any]] = {}

    def take(
        self, batch_id: int, action: str, token: str | None
    ) -> tuple[bool, dict[str, Any] | None]:
        if not token:
            return True, None
        key = (batch_id, action, token)
        cached = self._seen.get(key)
        if cached is not None:
            return False, cached
        return True, None

    def remember(self, batch_id: int, action: str, token: str | None, result: dict[str, Any]) -> None:
        if token:
            self._seen[(batch_id, action, token)] = result


idempotency = IdempotencyStore()


class TallyService:
    # ------------------------------------------------------------------ 箱位
    def list_slots(self, vessel: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(SLOT_MODULE)
        if vessel:
            rows = [row for row in rows if row.get("vessel") == vessel]
        return rows

    def list_vessels(self) -> list[dict[str, Any]]:
        """按船汇总箱位，并标出已在待核批次里占用的箱位，避免重复建批。"""
        vessels: dict[str, dict[str, Any]] = {}
        for row in store.rows(SLOT_MODULE):
            card = vessels.setdefault(
                row["vessel"],
                {"vessel": row["vessel"], "voyage": row.get("voyage", ""), "total_slots": 0, "slot_ids": []},
            )
            card["total_slots"] += 1
            card["slot_ids"].append(row["id"])
        occupied: dict[int, int] = {}
        for batch in store.rows(BATCH_MODULE):
            if batch.get("status") == BATCH_REVIEWING:
                for entry in batch.get("entries", []):
                    if entry.get("status") != ENTRY_VERIFIED:
                        occupied[entry["slot_id"]] = batch["id"]
        for card in vessels.values():
            ids = card.pop("slot_ids")
            card["busy_slot_ids"] = [sid for sid in ids if sid in occupied]
        return sorted(vessels.values(), key=lambda item: item["vessel"])

    # ------------------------------------------------------------------ 批次
    def list_batches(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(BATCH_MODULE)
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return [self._serialize_batch(row) for row in rows]

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        batch = store.find(BATCH_MODULE, batch_id)
        return self._serialize_batch(batch) if batch else None

    def _find_batch_row(self, batch_id: int) -> dict[str, Any] | None:
        return store.find(BATCH_MODULE, batch_id)

    def create_batch(
        self, vessel: str, slot_ids: list[int], operator: str, client_token: str | None
    ) -> tuple[dict[str, Any] | None, str]:
        vessel = (vessel or "").strip()
        if not vessel:
            return None, "请先选择一条船"
        if not slot_ids:
            return None, "请至少勾选一个箱位"
        slots = {int(row["id"]): row for row in self.list_slots(vessel)}
        missing = [sid for sid in slot_ids if sid not in slots]
        if missing:
            return None, f"箱位 {','.join(map(str, missing))} 不属于「{vessel}」，不能混船建批"

        # 已在待核批次里（且有条目未核完）的箱位不允许重复进入待核区
        occupied: dict[int, str] = {}
        for batch in store.rows(BATCH_MODULE):
            if batch.get("status") != BATCH_REVIEWING:
                continue
            for entry in batch.get("entries", []):
                if entry.get("status") != ENTRY_VERIFIED:
                    occupied[entry["slot_id"]] = batch.get("batch_no", "")
        conflicts = [slots[sid]["slot_no"] for sid in slot_ids if sid in occupied]
        if conflicts:
            return None, f"箱位 {'、'.join(conflicts)} 已在待核批次中，请先核完再建批"

        batches = store.rows(BATCH_MODULE)
        batch = {
            "id": _next_id(batches),
            "batch_no": f"TALL-{date.today():%Y%m%d}-{len(batches) + 1:03d}",
            "vessel": vessel,
            "operator": (operator or "理货员").strip() or "理货员",
            "status": BATCH_REVIEWING,
            "created_at": date.today().isoformat(),
            "entries": [
                {
                    "id": _next_id(batches) * 100 + index,
                    "slot_id": sid,
                    "slot_no": slots[sid]["slot_no"],
                    "expected_qty": int(slots[sid]["expected_qty"]),
                    "actual_qty": None,
                    "damage": "",
                    "status": ENTRY_PENDING,
                    "reject_reason": "",
                }
                for index, sid in enumerate(slot_ids, start=1)
            ],
        }
        batches.append(batch)
        return self._serialize_batch(batch), ""

    def fill_batch(
        self,
        batch_id: int,
        items: list[dict[str, Any]],
        damage_text: str | None,
        apply_all: bool,
    ) -> tuple[dict[str, Any] | None, str]:
        batch = self._find_batch_row(batch_id)
        if batch is None:
            return None, f"理货批次 {batch_id} 不存在"
        entries = {int(entry["slot_id"]): entry for entry in batch["entries"] if entry.get("status") != ENTRY_VERIFIED}
        editable = set(entries)

        for item in items or []:
            slot_id = int(item.get("slot_id", 0))
            if slot_id not in editable:
                return None, f"箱位 {slot_id} 已核完，不能再改箱量"
            raw = item.get("actual_qty")
            if raw in (None, ""):
                entries[slot_id]["actual_qty"] = None
                continue
            try:
                qty = int(raw)
            except (TypeError, ValueError):
                return None, f"箱位 {entries[slot_id]['slot_no']} 的实收箱量必须是整数"
            if qty < 0:
                return None, f"箱位 {entries[slot_id]['slot_no']} 的实收箱量不能为负"
            entries[slot_id]["actual_qty"] = qty

        if damage_text is not None:
            text = str(damage_text).strip()
            targets = batch["entries"] if apply_all else list(entries.values())
            for entry in targets:
                if entry.get("status") != ENTRY_VERIFIED:
                    entry["damage"] = text
        return self._serialize_batch(batch), ""

    def submit_batch(
        self, batch_id: int, client_token: str | None, client_summary: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
        """整组提交核对。

        - 幂等：token 命中直接回放第一次结果；
        - 残损为空、箱量未填一律拦下；
        - 汇总箱量必须与明细对得上（以服务端明细重算为准）；
        - 只有核对一致（无溢短）的条目转已核；溢短条目留在待核区单列。
        """
        batch = self._find_batch_row(batch_id)
        if batch is None:
            return None, f"理货批次 {batch_id} 不存在", []

        allow, cached = idempotency.take(batch_id, "submit", client_token)
        if not allow:
            cached_batch = self.get_batch(batch_id)
            return cached_batch, f"重复提交已忽略，沿用第一次提交结果（{cached.get('message', '')}）", cached.get(
                "held", []
            )

        pending = [e for e in batch["entries"] if e.get("status") != ENTRY_VERIFIED]
        unfilled = [e["slot_no"] for e in pending if e.get("actual_qty") in (None, "")]
        if unfilled:
            return None, f"以下箱位还没填实收箱量：{'、'.join(unfilled)}", []
        no_damage = [e["slot_no"] for e in pending if not str(e.get("damage") or "").strip()]
        if no_damage:
            return None, f"残损情况未登记，不能转入已核：{'、'.join(no_damage)}（无残损请选「{DAMAGE_NONE}」）", []

        summary = self._summary(batch)
        if client_summary and not self._summary_matches(client_summary, summary):
            return None, "核对汇总箱量与明细对不上，请刷新明细后再提交", []

        held: list[dict[str, Any]] = []
        moved = 0
        for entry in pending:
            diff = _difference(entry)
            if diff == 0:
                entry["status"] = ENTRY_VERIFIED
                entry["reject_reason"] = ""
                moved += 1
            else:
                held.append({"slot_id": entry["slot_id"], "slot_no": entry["slot_no"], "difference": diff})
        if all(e["status"] == ENTRY_VERIFIED for e in batch["entries"]):
            batch["status"] = BATCH_DONE

        message = f"{moved} 条核对一致已转入已核"
        if held:
            message += f"；{len(held)} 条溢短留在待核区处理"
        result = {"message": message, "held": held}
        idempotency.remember(batch_id, "submit", client_token, result)
        return self._serialize_batch(batch), message, held

    def reject_entries(
        self, batch_id: int, slot_ids: list[int], reason: str, client_token: str | None
    ) -> tuple[dict[str, Any] | None, str]:
        """打回：只有被选中的已核条目退回待核区，其余已核条目保持不动。"""
        batch = self._find_batch_row(batch_id)
        if batch is None:
            return None, f"理货批次 {batch_id} 不存在"

        allow, cached = idempotency.take(batch_id, "reject", client_token)
        if not allow:
            return self.get_batch(batch_id), f"重复打回已忽略，沿用第一次结果（{cached.get('message', '')}）"

        if not slot_ids:
            return None, "请勾选要打回的条目"
        reason = (reason or "").strip()
        if not reason:
            return None, "请填写打回原因"

        by_slot = {int(e["slot_id"]): e for e in batch["entries"]}
        targets = [by_slot.get(sid) for sid in slot_ids if sid in by_slot]
        not_verified = [e["slot_no"] for e in targets if e.get("status") != ENTRY_VERIFIED]
        if not_verified:
            return None, f"这些条目不在已核区，无需打回：{'、'.join(not_verified)}"

        for entry in targets:
            entry["status"] = ENTRY_REJECTED
            entry["reject_reason"] = reason
        batch["status"] = BATCH_REVIEWING
        message = f"已打回 {len(targets)} 条，留在待核区重新核对；其余已核条目不动"
        idempotency.remember(batch_id, "reject", client_token, {"message": message})
        return self._serialize_batch(batch), message

    # ------------------------------------------------------------------ 汇总
    def _summary(self, batch: dict[str, Any]) -> dict[str, int]:
        entries = batch["entries"]
        filled = [e for e in entries if e.get("actual_qty") not in (None, "")]
        over = [e for e in filled if (_difference(e) or 0) > 0]
        short = [e for e in filled if (_difference(e) or 0) < 0]
        return {
            "total": len(entries),
            "verified": sum(1 for e in entries if e.get("status") == ENTRY_VERIFIED),
            "pending": sum(1 for e in entries if e.get("status") != ENTRY_VERIFIED),
            "filled": len(filled),
            "expected_qty": sum(int(e["expected_qty"]) for e in entries),
            "actual_qty": sum(int(e["actual_qty"]) for e in filled),
            "difference_qty": sum((_difference(e) or 0) for e in filled),
            "over_count": len(over),
            "short_count": len(short),
            "overshort_count": len(over) + len(short),
        }

    def _summary_matches(self, client_summary: dict[str, Any], server_summary: dict[str, int]) -> bool:
        for key in ("expected_qty", "actual_qty", "difference_qty"):
            if key in client_summary:
                try:
                    if int(client_summary[key]) != int(server_summary[key]):
                        return False
                except (TypeError, ValueError):
                    return False
        return True

    def _serialize_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        diff = _difference(entry)
        row = dict(entry)
        row["difference"] = diff
        row["is_overshort"] = bool(diff) if diff is not None else False
        row["damage_filled"] = bool(str(entry.get("damage") or "").strip())
        return row

    def _serialize_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        entries = [self._serialize_entry(dict(entry)) for entry in batch["entries"]]
        data = {k: v for k, v in batch.items() if k != "entries"}
        data["entries"] = entries
        data["overshort_entries"] = [e for e in entries if e["is_overshort"]]
        data["summary"] = self._summary(batch)
        return data

    # ------------------------------------------------- 兼容旧版单条状态流转
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
        entry = {"id": _next_id(rows)}
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
