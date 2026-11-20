"""画像卡片路由：消费标签计算和分群结果生成画像。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.schemas import ActionPayload, ActionResult
from app.services.profile_card import (
    build_profile_card,
    get_profile_result,
    get_profile_tag_detail,
)
from app.store import store

router = APIRouter(prefix="/api/profile-card", tags=["profile_card"])


@router.get("")
def index(page: int = 1, size: int = 20) -> dict[str, Any]:
    rows = store.rows("profile_card")
    total = len(rows)
    start = (page - 1) * size
    return {"items": rows[start : start + size], "total": total, "page": page, "size": size}




@router.get("/export")
def export_entries() -> dict[str, Any]:
    rows = store.rows("profile_card")
    return {"items": rows, "total": len(rows)}
@router.get("/{entry_id}")
def detail(entry_id: int) -> dict[str, Any]:
    row = store.find("profile_card", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.post("/{card_id}/build")
def build(card_id: str) -> ActionResult:
    """刷新画像卡片：按标签当前就绪结果重算并记录本次采用的输入版本。"""
    try:
        result = build_profile_card(card_id)
        return ActionResult(ok=True, message="Built", entry=result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{card_id}/result")
def result(card_id: str) -> dict[str, Any]:
    """回查已确认的卡片：返回最近一次刷新产物，总量与当时的标签数据一致。"""
    res = get_profile_result(card_id)
    if res is None:
        raise HTTPException(status_code=404, detail="Result not found")
    return res


@router.get("/{card_id}/tag-detail")
def tag_detail(card_id: str) -> dict[str, Any]:
    """画像标签明细：逐标签引用所消费的任务版本，便于核对。"""
    res = get_profile_tag_detail(card_id)
    if res is None:
        raise HTTPException(status_code=404, detail="Result not found")
    return res


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    row = store.find("profile_card", entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    action = payload.action
    row["status"] = action
    row["pending"] = False
    return ActionResult(ok=True, message=action, entry=row)
