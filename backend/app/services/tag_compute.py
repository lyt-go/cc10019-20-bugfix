"""标签计算引擎：执行标签规则的批量计算，支持全量/增量/差量计算。

计算结果按任务版本落产物：每个产物都带有 ``task_id`` + ``compute_version``，
下游（画像卡片、标签快照等）只能引用完整覆盖的版本，差量/增量结果不能当作
人群完整覆盖数使用。
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store
from app.schemas import TagComputeResult

# 只有成功完成的任务才可能产出可消费的结果
READY_EXEC_STATES = {"success"}
# 只有全量范围的计算才构成人群完整覆盖
FULL_COVERAGE_SCOPES = {"all"}


def task_version(task: dict[str, Any]) -> str:
    """任务当前版本的展示编号。

    任务行上有显式的 ``compute_version`` 时以它为准，否则按任务编号和重算
    轮次生成，保证同一任务每次重算都能得到可核对的版本号。
    """
    explicit = str(task.get("compute_version") or "").strip()
    if explicit:
        return explicit
    revision = int(task.get("compute_revision", 0) or 0)
    return f"{task.get('task_id', '')}-v{revision}"


def _is_full_coverage(scope: str) -> bool:
    return scope in FULL_COVERAGE_SCOPES


def _task_ready(task: dict[str, Any]) -> bool:
    """任务是否已经产出可消费的结果。"""
    return str(task.get("exec_state", "")) in READY_EXEC_STATES


def _task_rank(task: dict[str, Any]) -> tuple[int, int, str, int]:
    """选择计算任务的排序键：优先全量、再按重算轮次/完成时间/编号取最新。"""
    return (
        1 if _is_full_coverage(str(task.get("compute_scope", ""))) else 0,
        int(task.get("compute_revision", 0) or 0),
        str(task.get("computed_at", "") or ""),
        int(task.get("id", 0) or 0),
    )


def select_compute_task(tag_id: str) -> dict[str, Any] | None:
    """为标签挑选唯一要消费的计算任务。

    同一标签可能挂了多个任务（全量、增量、差量、重算任务），读取结果时不能
    把每一个都追加一遍，也不能随手取第一条：优先已经完成且为全量的最新任务，
    其次是已完成的最新任务，最后才回退到未就绪的任务用于展示状态。
    """
    tasks = store.find_all_by_field("tag_compute", "tag_id", tag_id)
    if not tasks:
        return None
    ready = [task for task in tasks if _task_ready(task)]
    if ready:
        return max(ready, key=_task_rank)
    return max(tasks, key=_task_rank)


def invalidate_dependents_of(task_id: str) -> int:
    """失效所有引用了该任务所属标签的卡片/快照产物。

    同一标签的多个任务会参与"选哪个结果"的匹配，任一任务重算都会让下游
    的选用结果失效，因此按标签整体失效，避免历史覆盖值残留。
    """
    task_row = store.find_by_field("tag_compute", "task_id", task_id)
    if task_row is None:
        return 0
    tag_id = str(task_row.get("tag_id", ""))
    removed = 0

    def card_consumes(value: dict[str, Any]) -> bool:
        inputs = value.get("input_versions") or {}
        if tag_id in inputs:
            return True
        return any((item or {}).get("task_id") == task_id for item in inputs.values())

    def snapshot_consumes(value: dict[str, Any]) -> bool:
        return any(
            (item or {}).get("task_id") == task_id or (item or {}).get("tag_id") == tag_id
            for item in (value.get("compute_results") or [])
        )

    removed += store.invalidate_artifacts("profile_card", card_consumes)
    removed += store.invalidate_artifacts("profile_card_tag_detail", card_consumes)
    removed += store.invalidate_artifacts("tag_snapshot", snapshot_consumes)
    return removed


def _eval_condition(user: dict[str, Any], logic: str, attr_defs: dict[str, dict[str, Any]]) -> bool:
    """简化条件求值：支持 AND/OR/NOT 和基本比较。"""
    logic = logic.strip()
    # 处理括号
    if logic.startswith("(") and logic.endswith(")"):
        return _eval_condition(user, logic[1:-1], attr_defs)

    # 处理 NOT
    if logic.upper().startswith("NOT "):
        return not _eval_condition(user, logic[4:], attr_defs)

    # 处理 AND
    if " AND " in logic.upper():
        parts = _split_by_op(logic, "AND")
        return all(_eval_condition(user, p, attr_defs) for p in parts)

    # 处理 OR
    if " OR " in logic.upper():
        parts = _split_by_op(logic, "OR")
        return any(_eval_condition(user, p, attr_defs) for p in parts)

    # 基本比较：field op value
    return _eval_atom(user, logic, attr_defs)


def _split_by_op(logic: str, op: str) -> list[str]:
    """按操作符分割，注意括号嵌套。"""
    parts: list[str] = []
    depth = 0
    current = ""
    tokens = logic.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        if tok.upper() == op and depth == 0:
            if current.strip():
                parts.append(current.strip())
            current = ""
        else:
            current += tok + " "
        i += 1
    if current.strip():
        parts.append(current.strip())
    return parts if len(parts) > 1 else [logic]


def _eval_atom(user: dict[str, Any], logic: str, attr_defs: dict[str, Any]) -> bool:
    """求值原子条件。"""
    logic = logic.strip()
    # 支持 =, !=, <, >, <=, >=, IN, NOT IN
    for op in ["!=", "<=", ">=", "=", "<", ">"]:
        if op in logic:
            parts = logic.split(op, 1)
            if len(parts) == 2:
                left = parts[0].strip()
                right = parts[1].strip()
                left_val = user.get(left)
                # 处理字符串引号
                if right.startswith("'") and right.endswith("'"):
                    right = right[1:-1]
                elif right.startswith('"') and right.endswith('"'):
                    right = right[1:-1]
                # 处理数值
                try:
                    right = float(right)
                    if left_val is not None:
                        try:
                            left_val = float(left_val)
                        except ValueError:
                            pass
                except ValueError:
                    pass
                # 处理布尔
                if right == "True":
                    right = True
                elif right == "False":
                    right = False

                if op == "=":
                    return left_val == right
                elif op == "!=":
                    return left_val != right
                elif op == "<":
                    if left_val is None:
                        return False
                    return left_val < right
                elif op == ">":
                    if left_val is None:
                        return False
                    return left_val > right
                elif op == "<=":
                    if left_val is None:
                        return False
                    return left_val <= right
                elif op == ">=":
                    if left_val is None:
                        return False
                    return left_val >= right
    # 处理 IN
    if " IN " in logic.upper():
        parts = logic.upper().split(" IN ", 1)
        field = logic[:len(parts[0])].strip()
        val_list_str = logic[len(parts[0])+4:].strip()
        if val_list_str.startswith("[") and val_list_str.endswith("]"):
            val_list_str = val_list_str[1:-1]
        vals = [v.strip().strip("'\"").strip() for v in val_list_str.split(",")]
        field_val = str(user.get(field, ""))
        return field_val in vals

    return bool(user.get(logic, False))


def compute_tag(task_id: str, invalidate_dependents: bool = True) -> TagComputeResult:
    """执行标签计算任务。

    写入新结果前会失效引用了该任务的卡片/快照产物，避免历史覆盖值继续残留
    在下游结果里；下游下次读取时会按新版本重建。
    """
    task_row = store.find_by_field("tag_compute", "task_id", task_id)
    if task_row is None:
        raise ValueError(f"Task not found: {task_id}")

    tag_id = task_row["tag_id"]
    tag = store.find_by_field("tag_define", "tag_id", tag_id)
    if tag is None:
        raise ValueError(f"Tag not found: {tag_id}")

    start_time = time.time()
    scope = task_row["compute_scope"]
    revision = int(task_row.get("compute_revision", 0) or 0)
    version = str(task_row.get("compute_version") or f"{task_id}-v{revision}")
    full_coverage = _is_full_coverage(scope)

    if invalidate_dependents:
        invalidate_dependents_of(task_id)

    # 获取属性定义用于类型转换
    attr_defs = {}
    for attr in store.rows("attr_dict"):
        attr_defs[attr["attr_id"]] = attr

    # 模拟用户数据（实际应从事件数据生成）
    # 这里使用内存中的示例用户数据
    users = _load_users_for_tag(tag, attr_defs, scope)

    # 计算标签值
    results: list[dict[str, Any]] = []
    for user in users:
        matched = _eval_condition(user, tag["compute_logic"], attr_defs)
        results.append({
            "user_id": user.get("user_id"),
            "tag_id": tag_id,
            "tag_value": matched,
            "compute_time": task_row.get("output_partition", ""),
        })

    computed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # 存储计算结果产物：版本是对外可核对的消费依据
    result_data = {
        "task_id": task_id,
        "tag_id": tag_id,
        "compute_scope": scope,
        "compute_version": version,
        "task_version": version,
        "is_full_coverage": full_coverage,
        # 差量/增量只覆盖部分用户，不能当作人群完整覆盖数
        "coverage_count": len(results) if full_coverage else None,
        "result_count": len(results),
        "user_tag_values": results[:100],  # 只存储前100条示例
        "output_partition": task_row.get("output_partition", ""),
        "compute_time_ms": int((time.time() - start_time) * 1000),
        "computed_at": computed_at,
    }
    store.set_artifact("tag_compute", task_id, result_data)

    # 更新任务状态：版本号随本次执行写入任务行，便于回查
    store.update("tag_compute", task_row["id"], {
        "exec_state": "success",
        "status": "已完成",
        "pending": False,
        "result_count": len(results),
        "compute_version": version,
        "compute_revision": revision,
        "computed_at": computed_at,
        "error_log": "",
    })

    return TagComputeResult(**result_data)


def _load_users_for_tag(tag: dict[str, Any], attr_defs: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    """加载需要计算标签的用户数据。"""
    # 模拟用户数据 - 基于属性定义生成
    users = []
    for i in range(1, 126):
        user = {
            "user_id": f"U{i:05d}",
            "ATTR-001": ["app", "web", "miniapp", "h5"][i % 4],
            "ATTR-002": i % 60,
            "ATTR-003": float(i * 15.5),
            "ATTR-004": ["ios", "android", "pc", "other"][i % 4],
            "ATTR-005": ["普通", "银卡", "金卡", "钻石"][i % 4],
        }
        # 增量模式只处理新用户
        if scope == "incremental" and i > 32:
            continue
        # 差量模式只处理变化用户
        if scope == "delta" and i % 3 != 0:
            continue
        users.append(user)
    return users


def get_compute_result(task_id: str) -> dict[str, Any] | None:
    """获取标签计算结果。

    只有任务已经成功完成且产出的产物版本与任务当前版本一致时才返回，
    避免把上一轮/历史残留的结果当成本次计算输入。
    """
    task_row = store.find_by_field("tag_compute", "task_id", task_id)
    if task_row is None:
        return None
    artifact = store.get_artifact("tag_compute", task_id)
    if artifact and _artifact_matches_task(artifact, task_row):
        return artifact
    return None


def ensure_compute_result(task_id: str) -> dict[str, Any] | None:
    """读取结果；任务已完成但产物缺失/版本过期时按当前版本补算一次。

    仅对 exec_state=success 的任务生效，计算中/失败的任务返回 None。
    """
    artifact = get_compute_result(task_id)
    if artifact:
        return artifact
    task_row = store.find_by_field("tag_compute", "task_id", task_id)
    if task_row is None or not _task_ready(task_row):
        return None
    try:
        return compute_tag(task_id).model_dump()
    except ValueError:
        return None


def _artifact_matches_task(artifact: dict[str, Any], task_row: dict[str, Any]) -> bool:
    if str(task_row.get("exec_state", "")) not in READY_EXEC_STATES:
        return False
    return str(artifact.get("compute_version", "")) == task_version(task_row)


def list_compute_results() -> list[dict[str, Any]]:
    """列出所有标签计算结果。"""
    return store.list_artifacts("tag_compute")


def recompute_tag(task_id: str, new_scope: str | None = None) -> TagComputeResult:
    """重新计算标签，支持切换计算范围。

    重算会推进任务版本，并在计算前失效引用了旧版本的卡片/快照产物，
    保证再次读取时不会把历史覆盖值当成最新结果。
    """
    task_row = store.find_by_field("tag_compute", "task_id", task_id)
    if task_row is None:
        raise ValueError(f"Task not found: {task_id}")

    revision = int(task_row.get("compute_revision", 0) or 0) + 1
    updates: dict[str, Any] = {
        "compute_revision": revision,
        "compute_version": f"{task_id}-v{revision}",
        "exec_state": "running",
        "pending": True,
        "status": "计算中",
    }
    if new_scope:
        updates["compute_scope"] = new_scope
    store.update("tag_compute", task_row["id"], updates)
    store.invalidate_compute_cache(f"tag_compute:{task_id}")

    return compute_tag(task_id, invalidate_dependents=True)
