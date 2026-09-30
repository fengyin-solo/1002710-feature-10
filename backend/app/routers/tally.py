"""理货记录接口：维护理货记录，覆盖开始理货、提交核对、登记溢短等动作。

批次理货：同一条船的箱位整组进待核区、逐条核对、溢短单列、打回重来。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.tally import TallyBatchService, TallyService

router = APIRouter(prefix="/api/tally", tags=["理货记录"])

service = TallyService()
batch_service = TallyBatchService()

LIST_FIELDS = ["理货编号", "对应船舶", "箱量核对", "残损记录", "溢短记录", "理货人员", "理货时间", "理货状态"]
STATUSES = ["待理货", "理货中", "已核对", "有溢短"]


@router.get("/batches", response_model=PageResult[dict])
def list_batches(
    keyword: str | None = Query(default=None, description="按批次号或对应船舶检索"),
    status: str | None = Query(default=None, description="待核中、溢短待处理、已核完成"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """理货批次列表：每批带箱量汇总，溢短数量一眼能看到。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = batch_service.list_batches(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/batches", response_model=ActionResult)
def create_batch(payload: EntryPayload) -> ActionResult:
    """整组登记：同一条船的箱位多选后一次进待核区；同一批次号重复提交只认第一次。"""
    batch, message, duplicated = batch_service.create_batch(payload.values)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/batches/{batch_id}", response_model=dict)
def get_batch(batch_id: int) -> dict:
    """批次详情：条目、汇总与自动挑出的溢短清单一并返回。"""
    batch = batch_service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"理货批次 {batch_id} 不存在或已归档")
    return batch


@router.post("/batches/{batch_id}/verify", response_model=ActionResult)
def verify_batch(batch_id: int, payload: EntryPayload) -> ActionResult:
    """提交核对：残损统一录、箱量逐条对；一致的转已核，溢短的自动单列。"""
    batch, message = batch_service.submit_verify(batch_id, payload.values)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("/batches/{batch_id}/reject", response_model=ActionResult)
def reject_batch_items(batch_id: int, payload: EntryPayload) -> ActionResult:
    """打回指定条目：退回待核区等重来，已核完的不动。"""
    positions = payload.values.get("箱位")
    if not isinstance(positions, list):
        return ActionResult(ok=False, message="请用「箱位」字段传入要打回的箱位编号列表")
    batch, message = batch_service.reject_items(batch_id, positions)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


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
