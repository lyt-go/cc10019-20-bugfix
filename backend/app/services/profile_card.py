"""画像卡片服务：消费标签计算和分群结果，生成画像卡片数据。

读取标签计算结果时有两条约束：

1. 每个标签只匹配一个计算任务。同一标签可能同时挂着全量、增量、差量等任务，
   逐个追加会让卡片上同一个标签出现多份覆盖人数；这里按"已完成后优先全量、
   再取最新版本"的规则选唯一任务。
2. 差量/增量结果只是变化量，不能当作人群完整覆盖数。覆盖人数只取全量结果，
   差量只作为变化量单独标注。

每次刷新都会把实际采用的任务版本写进卡片产物，并额外落一份画像标签明细，
便于再次进入原卡片核对本次用的是哪个结果。
"""
from __future__ import annotations

import time
from typing import Any

from app.schemas import ProfileCardResult, TagConsumption
from app.services.tag_compute import (
    _is_full_coverage,
    _task_ready,
    ensure_compute_result,
    select_compute_task,
    task_version,
)
from app.services.user_segment import get_segment_result
from app.store import store

# 卡片结果产物、标签明细产物的类型标识
CARD_ARTIFACT = "profile_card"
DETAIL_ARTIFACT = "profile_card_tag_detail"

# 展示状态文案
STATUS_READY = "已就绪"
STATUS_RUNNING = "计算中"
STATUS_NOT_READY = "未产出"
STATUS_FULL = "全量"
STATUS_DELTA = "差量"
STATUS_INCREMENTAL = "增量"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_profile_card(card_id: str) -> dict[str, Any]:
    """构建画像卡片，消费标签计算和分群结果。"""
    card = store.find_by_field("profile_card", "card_id", card_id)
    if card is None:
        raise ValueError(f"Profile card not found: {card_id}")

    display_tags = list(dict.fromkeys(card.get("display_tags", [])))
    segment_id = card.get("segment_id", "")
    built_at = _now()

    tag_values: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    input_versions: dict[str, dict[str, Any]] = {}
    pending_tags: list[str] = []
    coverage_values: list[int] = []

    for tag_id in display_tags:
        tag_name = _get_tag_name(tag_id)
        task = _select_task_for_tag(tag_id)
        consumption = _resolve_consumption(tag_id, tag_name, task)

        input_versions[tag_id] = consumption.model_dump()
        tag_values.append(_tag_value_entry(consumption))
        details.append({
            **consumption.model_dump(),
            "card_id": card_id,
            "card_name": card["card_name"],
            "built_at": built_at,
        })

        if not consumption.ready:
            pending_tags.append(tag_id)
        elif consumption.is_full_coverage and consumption.coverage_count is not None:
            coverage_values.append(int(consumption.coverage_count))

    # 获取分群结果
    segment_info = None
    if segment_id:
        seg_result = get_segment_result(segment_id)
        if seg_result:
            segment_info = {
                "segment_id": segment_id,
                "segment_name": _get_segment_name(segment_id),
                "user_count": seg_result.get("user_count", 0),
                "tier_distribution": seg_result.get("tier_distribution", {}),
            }

    # 卡片展示的覆盖总量只累加全量结果，差量不作为覆盖数计入
    result = ProfileCardResult(
        card_id=card_id,
        card_name=card["card_name"],
        display_tags=tag_values,
        segment_info=segment_info,
        data_source=card["data_source"],
        display_style=card["display_style"],
        total_coverage=sum(coverage_values) if coverage_values else None,
        pending_tags=pending_tags,
        input_versions=input_versions,
        built_at=built_at,
    ).model_dump()

    # 存储卡片产物与标签明细（明细逐标签可回查）
    store.set_artifact(CARD_ARTIFACT, card_id, result)
    store.set_artifact(DETAIL_ARTIFACT, card_id, {
        "card_id": card_id,
        "card_name": card["card_name"],
        "built_at": built_at,
        "input_versions": input_versions,
        "pending_tags": pending_tags,
        "total_coverage": result["total_coverage"],
        "tags": details,
    })

    # 更新状态：有标签就绪才视为已发布；仍有未就绪标签时保留待刷新标记
    has_ready_tag = any(tag.get("ready") for tag in tag_values)
    updates: dict[str, Any] = {"pending": bool(pending_tags)}
    if has_ready_tag and card.get("status") != "已停用":
        updates["status"] = "已发布"
    store.update("profile_card", card["id"], updates)

    return result


def _select_task_for_tag(tag_id: str) -> dict[str, Any] | None:
    """同一标签的多个计算任务里挑出唯一要被消费的那个。"""
    return select_compute_task(tag_id)


def _resolve_consumption(tag_id: str, tag_name: str, task: dict[str, Any] | None) -> TagConsumption:
    """把任务的当前状态翻译成卡片可展示的消费说明。"""
    if task is None:
        return TagConsumption(
            tag_id=tag_id,
            tag_name=tag_name,
            ready=False,
            status=STATUS_NOT_READY,
        )

    task_id = str(task.get("task_id", ""))
    scope = str(task.get("compute_scope", ""))
    version = task_version(task)
    ready = _task_ready(task)
    # 已完成任务的结果缺失或版本过期时按当前版本补算；计算中/失败任务保持未就绪
    result = ensure_compute_result(task_id) if ready else None

    if not ready or result is None:
        return TagConsumption(
            tag_id=tag_id,
            tag_name=tag_name,
            task_id=task_id,
            compute_version=version,
            compute_scope=scope,
            is_full_coverage=_is_full_coverage(scope),
            ready=False,
            status=STATUS_RUNNING if task.get("exec_state") == "running" else STATUS_NOT_READY,
        )

    full_coverage = _is_full_coverage(scope)
    result_count = int(result.get("result_count", 0))
    coverage_count = int(result.get("coverage_count", 0)) if full_coverage and result.get(
        "coverage_count"
    ) is not None else None

    return TagConsumption(
        tag_id=tag_id,
        tag_name=tag_name,
        task_id=task_id,
        compute_version=str(result.get("compute_version") or version),
        compute_scope=scope,
        is_full_coverage=full_coverage,
        ready=True,
        coverage_count=coverage_count,
        result_count=result_count,
        status=STATUS_READY if full_coverage else _scope_label(scope),
    )


def _tag_value_entry(consumption: TagConsumption) -> dict[str, Any]:
    """卡片展示层：覆盖人数只对全量结果给出，差量明确标注为变化量。"""
    entry: dict[str, Any] = {
        "tag_id": consumption.tag_id,
        "tag_name": consumption.tag_name,
        "task_id": consumption.task_id,
        "compute_version": consumption.compute_version,
        "compute_scope": consumption.compute_scope,
        "is_full_coverage": consumption.is_full_coverage,
        "ready": consumption.ready,
        "status": consumption.status,
        # 人群完整覆盖数：非全量结果不下发，避免把差量当覆盖数
        "coverage_count": consumption.coverage_count,
        "result_count": consumption.result_count,
    }
    if not consumption.ready:
        entry["result_count"] = 0
    return entry


def _scope_label(scope: str) -> str:
    return {
        "delta": STATUS_DELTA,
        "incremental": STATUS_INCREMENTAL,
    }.get(scope, scope or STATUS_READY)


def _get_tag_name(tag_id: str) -> str:
    """获取标签名称。"""
    tag = store.find_by_field("tag_define", "tag_id", tag_id)
    return tag["tag_name"] if tag else tag_id


def _get_segment_name(segment_id: str) -> str:
    """获取分群名称。"""
    seg = store.find_by_field("user_segment", "segment_id", segment_id)
    return seg["segment_name"] if seg else segment_id


def get_profile_result(card_id: str) -> dict[str, Any] | None:
    """获取画像卡片结果。

    已确认的卡片回查时返回最近一次刷新的产物，保证回查总量与卡片当时
    采用的标签数据一致；没有产物时补建一次。
    """
    artifact = store.get_artifact(CARD_ARTIFACT, card_id)
    if artifact:
        return artifact
    try:
        return build_profile_card(card_id)
    except ValueError:
        return None


def get_profile_tag_detail(card_id: str) -> dict[str, Any] | None:
    """获取画像标签明细：逐标签引用所消费的任务版本，便于回查。"""
    detail = store.get_artifact(DETAIL_ARTIFACT, card_id)
    if detail:
        return detail
    if store.find_by_field("profile_card", "card_id", card_id) is None:
        return None
    try:
        build_profile_card(card_id)
    except ValueError:
        return None
    return store.get_artifact(DETAIL_ARTIFACT, card_id)
