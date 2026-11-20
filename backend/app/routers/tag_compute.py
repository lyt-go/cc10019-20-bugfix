"""标签计算路由：执行标签计算任务并获取结果。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.schemas import ActionPayload, ActionResult
from app.services.tag_compute import (
    compute_tag,
    get_compute_result,
    list_compute_results,
    recompute_tag,
    select_compute_task,
    task_version,
)
from app.store import store

router = APIRouter(prefix="/api/tag-compute", tags=["tag_compute"])


@router.get("")
def index(page: int = 1, size: int = 20) -> dict[str, Any]:
    rows = store.rows("tag_compute")
    total = len(rows)
    start = (page - 1) * size
    return {"items": rows[start : start + size], "total": total, "page": page, "size": size}




@router.get("/export")
def export_entries() -> dict[str, Any]:
    rows = store.rows("tag_compute")
    return {"items": rows, "total": len(rows)}
@router.get("/{entry_id}")
def detail(entry_id: int) -> dict[str, Any]:
    row = store.find("tag_compute", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.post("/{task_id}/run")
def run(task_id: str) -> ActionResult:
    try:
        result = compute_tag(task_id)
        return ActionResult(ok=True, message="Computed", entry=result.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{task_id}/result")
def result(task_id: str) -> dict[str, Any]:
    res = get_compute_result(task_id)
    if res is None:
        raise HTTPException(status_code=404, detail="Result not found")
    return res


@router.get("/by-tag/{tag_id}/result")
def result_by_tag(tag_id: str) -> dict[str, Any]:
    """按标签读取结果：同一标签只返回唯一被选中的任务结果。"""
    task = select_compute_task(tag_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Result not found")
    res = get_compute_result(task["task_id"])
    if res is None:
        raise HTTPException(status_code=404, detail="Result not ready")
    return {
        **res,
        "selected_task_id": task["task_id"],
        "task_version": task_version(task),
    }


@router.get("/artifacts/all")
def artifacts() -> list[dict[str, Any]]:
    return list_compute_results()


@router.post("/{task_id}/recompute")
def recompute(task_id: str, payload: dict[str, Any] | None = None) -> ActionResult:
    scope = payload.get("scope") if payload else None
    try:
        result = recompute_tag(task_id, scope)
        return ActionResult(ok=True, message="Recomputed", entry=result.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    row = store.find("tag_compute", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    action = payload.action
    row["status"] = action
    row["pending"] = False
    return ActionResult(ok=True, message=action, entry=row)
