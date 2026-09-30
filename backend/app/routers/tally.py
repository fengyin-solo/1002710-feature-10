"""理货记录接口。

除兼容旧版单条登记/状态流转外，提供批量核对工作流：
箱位多选建批 → 待核区填报 → 溢短自动单列 → 一致转已核 → 打回。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.tally import BATCH_DONE, BATCH_REVIEWING, TallyService

router = APIRouter(prefix="/api/tally", tags=["理货记录"])

service = TallyService()

LIST_FIELDS = ["理货编号", "对应船舶", "箱量核对", "残损记录", "溢短记录", "理货人员", "理货时间", "理货状态"]
STATUSES = ["待理货", "理货中", "已核对", "有溢短"]


class BatchCreatePayload(BaseModel):
    vessel: str
    slot_ids: list[int] = Field(default_factory=list)
    operator: str | None = None
    client_token: str | None = None


class FillItem(BaseModel):
    slot_id: int
    actual_qty: int | None = None


class BatchFillPayload(BaseModel):
    items: list[FillItem] = Field(default_factory=list)
    damage: str | None = None
    apply_all: bool = False


class BatchSubmitPayload(BaseModel):
    client_token: str | None = None
    summary: dict[str, Any] | None = None


class BatchRejectPayload(BaseModel):
    slot_ids: list[int] = Field(default_factory=list)
    reason: str = ""
    client_token: str | None = None


class BatchResult(BaseModel):
    ok: bool
    message: str
    batch: dict[str, Any] | None = None
    held: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------- 批量核对流
@router.get("/work/vessels")
def work_vessels() -> dict[str, Any]:
    """按船列出箱位与占用情况，供建批前多选箱位。"""
    return {"items": service.list_vessels()}


@router.get("/work/slots")
def work_slots(vessel: str = Query(description="船名，只看该船箱位")) -> dict[str, Any]:
    return {"items": service.list_slots(vessel)}


@router.get("/work/batches")
def work_batches(status: str | None = Query(default=None, description=f"{BATCH_REVIEWING}/{BATCH_DONE}")) -> dict[str, Any]:
    """待核区 + 已核完批次列表。"""
    items = service.list_batches(status=status)
    return {"items": items, "total": len(items)}


@router.get("/work/batches/{batch_id}")
def work_batch_detail(batch_id: int) -> dict[str, Any]:
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"理货批次 {batch_id} 不存在")
    return batch


@router.post("/work/batches", response_model=BatchResult)
def work_create_batch(payload: BatchCreatePayload) -> BatchResult:
    """同一条船的多个箱位整组建批，进入待核区。"""
    batch, message = service.create_batch(
        payload.vessel, payload.slot_ids, payload.operator or "", payload.client_token
    )
    if batch is None:
        return BatchResult(ok=False, message=message)
    return BatchResult(ok=True, message=f"批次 {batch['batch_no']} 已进入待核区，共 {len(batch['entries'])} 个箱位", batch=batch)


@router.post("/work/batches/{batch_id}/fill", response_model=BatchResult)
def work_fill_batch(batch_id: int, payload: BatchFillPayload) -> BatchResult:
    """逐条填报实收箱量、统一登记残损；溢短由系统按差额自动判定。"""
    items = [item.model_dump() for item in payload.items]
    batch, message = service.fill_batch(batch_id, items, payload.damage, payload.apply_all)
    if batch is None:
        return BatchResult(ok=False, message=message)
    return BatchResult(ok=True, message="核对数据已暂存", batch=batch)


@router.post("/work/batches/{batch_id}/submit", response_model=BatchResult)
def work_submit_batch(batch_id: int, payload: BatchSubmitPayload) -> BatchResult:
    """整组提交核对：一致条目转已核，溢短条目留在待核区；重复提交只认第一次。"""
    batch, message, held = service.submit_batch(batch_id, payload.client_token, payload.summary)
    if batch is None:
        return BatchResult(ok=False, message=message)
    return BatchResult(ok=True, message=message, batch=batch, held=held)


@router.post("/work/batches/{batch_id}/reject", response_model=BatchResult)
def work_reject_batch(batch_id: int, payload: BatchRejectPayload) -> BatchResult:
    """打回勾选的已核条目：被打回的退回待核区，其余已核条目不动。"""
    batch, message = service.reject_entries(batch_id, payload.slot_ids, payload.reason, payload.client_token)
    if batch is None:
        return BatchResult(ok=False, message=message)
    return BatchResult(ok=True, message=message, batch=batch)


# ---------------------------------------------------------- 旧版单条接口保留
@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按理货编号检索"),
    status: str | None = Query(default=None, description="待理货、理货中、已核对、有溢短"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按理货编号与状态过滤理货记录列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出理货记录清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "tally", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条理货记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"理货记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条理货记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="理货记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条理货记录执行开始理货、提交核对、登记溢短；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
