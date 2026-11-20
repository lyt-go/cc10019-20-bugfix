"""用户分群服务：基于标签计算结果生成用户分群。"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.schemas import SegmentResult
from app.services.tag_compute import ensure_compute_result, select_compute_task


def compute_segment(segment_id: str) -> SegmentResult:
    """执行分群计算，消费标签计算结果。"""
    segment = store.find_by_field("user_segment", "segment_id", segment_id)
    if segment is None:
        raise ValueError(f"Segment not found: {segment_id}")

    tier_rules = segment.get("tier_rules", [])

    # 获取标签计算结果：每个标签只取唯一选中的任务结果
    tag_results: dict[str, dict[str, Any]] = {}
    for rule in tier_rules:
        tag_id = rule.get("tag", "")
        if tag_id in tag_results:
            continue
        task = select_compute_task(tag_id)
        if task is None:
            continue
        result = ensure_compute_result(task["task_id"])
        if result:
            tag_results[tag_id] = result

    # 模拟用户分层
    user_count = segment.get("size_estimate", 1000)
    tier_distribution: dict[str, int] = {}

    # 基于分层规则分配
    for rule in tier_rules:
        tag_id = rule.get("tag", "")
        matched = 1 if tag_id in tag_results else 0
        if matched > 0:
            tier_distribution[tag_id] = tag_results[tag_id].get("result_count", 0)
        else:
            tier_distribution[tag_id] = user_count // max(len(tier_rules), 1)

    if not tier_distribution:
        tier_distribution["default"] = user_count

    result = SegmentResult(
        segment_id=segment_id,
        segment_name=segment["segment_name"],
        user_count=user_count,
        tier_distribution=tier_distribution,
        output_set=f"segment_{segment_id}",
    )

    # 存储产物
    store.set_artifact("user_segment", segment_id, result.model_dump())

    # 更新状态
    store.update("user_segment", segment["id"], {
        "status": "已就绪",
        "pending": False,
    })

    return result


def get_segment_result(segment_id: str) -> dict[str, Any] | None:
    """获取分群结果。"""
    artifact = store.get_artifact("user_segment", segment_id)
    if artifact:
        return artifact
    try:
        result = compute_segment(segment_id)
        return result.model_dump()
    except ValueError:
        return None
