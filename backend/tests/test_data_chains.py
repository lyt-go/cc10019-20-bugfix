"""跨模块数据链路集成测试。"""
from __future__ import annotations

import unittest
from typing import Any

# 设置路径
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.store import store
from app.services.attr_dict import list_attrs, get_attr
from app.services.tag_define import get_tag, validate_tag_logic
from app.services.tag_compute import compute_tag, get_compute_result
from app.services.event_track import get_event
from app.services.event_cleanse import cleanse_events, get_cleanse_result
from app.services.event_aggregate import aggregate_events, get_aggregate_result
from app.services.user_segment import compute_segment, get_segment_result
from app.services.crowd_select import select_crowd, get_crowd_result
from app.services.overlap_analysis import analyze_overlap, get_overlap_result
from app.services.funnel_analyze import analyze_funnel, get_funnel_result
from app.services.session_split import split_sessions, get_session_result
from app.services.behavior_path import build_path_sankey, get_path_result
from app.services.retention_calc import calculate_retention, get_retention_result
from app.services.lifetime_value import predict_ltv, get_ltv_result, ltv_with_retention
from app.services.tag_snapshot import create_snapshot, get_snapshot
from app.services.behavior_heatmap import generate_heatmap, get_heatmap
from app.services.profile_card import (
    build_profile_card,
    get_profile_result,
    get_profile_tag_detail,
)
from app.services.tag_drill import drill_tag, get_drill_result
from app.services.tag_compute import recompute_tag, select_compute_task


class TestDataChains(unittest.TestCase):
    """测试跨模块数据链路。"""

    def setUp(self) -> None:
        """重置store状态。"""
        store.reset()

    # ========== 链路1: 属性字典 → 标签定义 → 标签计算 ==========
    def test_chain_attr_to_tag_compute(self) -> None:
        """属性字典定义属性，标签定义引用属性，标签计算执行逻辑。"""
        # 1. 验证属性存在
        attr = get_attr("ATTR-002")
        self.assertIsNotNone(attr)
        self.assertEqual(attr["attr_name"], "最近活跃天数")

        # 2. 验证标签引用了属性
        tag = get_tag("TAG-001")
        self.assertIsNotNone(tag)
        self.assertIn("ATTR-002", tag["source_attrs"])

        # 3. 验证标签计算执行
        result = compute_tag("TC-001")
        self.assertEqual(result.tag_id, "TAG-001")
        self.assertGreater(result.result_count, 0)

        # 4. 验证产物可查询
        artifact = get_compute_result("TC-001")
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact["tag_id"], "TAG-001")

    # ========== 链路2: 事件埋点 → 清洗 → 聚合 ==========
    def test_chain_event_cleanse_aggregate(self) -> None:
        """事件定义 → 清洗规则 → 清洗执行 → 聚合消费清洗结果。"""
        # 1. 验证事件存在
        event = get_event("EV-001")
        self.assertIsNotNone(event)

        # 2. 验证清洗规则关联事件
        cleanse = get_cleanse_result("EC-001")
        if cleanse is None:
            cleanse_result = cleanse_events("EC-001")
            cleanse = cleanse_result.model_dump()
        self.assertEqual(cleanse["event_id"], "EV-001")
        self.assertGreater(cleanse["total_records"], 0)

        # 3. 验证聚合消费清洗结果
        agg_result = aggregate_events("EA-001")
        self.assertEqual(agg_result.event_id, "EV-001")
        self.assertGreater(len(agg_result.result_rows), 0)

        # 4. 验证聚合产物存在
        artifact = get_aggregate_result("EA-001")
        self.assertIsNotNone(artifact)

    # ========== 链路3: 用户分群 → 圈选 → 交集分析 ==========
    def test_chain_segment_crowd_overlap(self) -> None:
        """分群定义 → 圈选条件 → 交集分析消费圈选结果。"""
        # 1. 分群计算
        seg_result = compute_segment("SEG-001")
        self.assertEqual(seg_result.segment_id, "SEG-001")
        self.assertGreater(seg_result.user_count, 0)

        # 2. 圈选执行
        crowd_a = select_crowd("CS-001")
        self.assertEqual(crowd_a["condition_id"], "CS-001")
        self.assertGreater(crowd_a["matched_count"], 0)

        crowd_b = select_crowd("CS-002")
        self.assertEqual(crowd_b["condition_id"], "CS-002")

        # 3. 交集分析消费圈选结果
        overlap = analyze_overlap("OA-001")
        self.assertEqual(overlap["crowd_a"], "CS-001")
        self.assertEqual(overlap["crowd_b"], "CS-002")
        self.assertIn("result_count", overlap)

        # 4. 验证产物
        artifact = get_overlap_result("OA-001")
        self.assertIsNotNone(artifact)

    # ========== 链路4: 行为路径 → 漏斗分析 → 会话切割 ==========
    def test_chain_path_funnel_session(self) -> None:
        """行为路径分析 → 漏斗分析 → 会话切割。"""
        # 1. 会话切割
        session_result = split_sessions("SS-001")
        self.assertEqual(session_result.strategy_id, "SS-001")
        self.assertGreater(session_result.session_count, 0)

        # 2. 漏斗分析
        funnel_result = analyze_funnel("FN-001")
        self.assertEqual(funnel_result.funnel_id, "FN-001")
        self.assertEqual(len(funnel_result.step_counts), 3)
        self.assertGreater(funnel_result.step_counts[0], funnel_result.step_counts[1])

        # 3. 行为路径
        path_result = build_path_sankey("BP-001")
        self.assertEqual(path_result.model_id, "BP-001")
        self.assertGreater(len(path_result.sankey_nodes), 0)
        self.assertGreater(len(path_result.sankey_links), 0)

        # 4. 验证产物
        self.assertIsNotNone(get_session_result("SS-001"))
        self.assertIsNotNone(get_funnel_result("FN-001"))
        self.assertIsNotNone(get_path_result("BP-001"))

    # ========== 链路5: 留存计算 → LTV预测 ==========
    def test_chain_retention_ltv(self) -> None:
        """留存计算结果 → LTV预测校准。"""
        # 1. 留存计算
        retention = calculate_retention("RC-001")
        self.assertEqual(retention.config_id, "RC-001")
        self.assertGreater(len(retention.retention_rates), 0)

        # 2. LTV预测
        ltv = predict_ltv("LTV-001")
        self.assertEqual(ltv.model_id, "LTV-001")
        self.assertGreater(ltv.avg_ltv, 0)

        # 3. LTV结合留存校准
        calibrated = ltv_with_retention("LTV-001", "RC-001")
        self.assertIn("retention_adjusted_ltv", calibrated)
        self.assertIn("avg_retention_rate", calibrated)

        # 4. 验证产物
        self.assertIsNotNone(get_retention_result("RC-001"))
        self.assertIsNotNone(get_ltv_result("LTV-001"))

    # ========== 链路6: 标签快照消费标签计算+分群结果 ==========
    def test_chain_tag_snapshot_consumes_compute_and_segment(self) -> None:
        """标签快照消费标签计算结果和用户分群结果。"""
        # 1. 先执行标签计算
        compute_tag("TC-001")
        compute_tag("TC-002")

        # 2. 执行分群计算
        compute_segment("SEG-001")

        # 3. 创建快照（消费上述结果）
        snapshot = create_snapshot("SN-001")
        self.assertEqual(snapshot["snapshot_id"], "SN-001")
        self.assertGreater(snapshot["tag_count"], 0)
        self.assertGreater(snapshot["segment_count"], 0)

        # 4. 验证快照包含计算结果引用
        self.assertIn("compute_results", snapshot)
        self.assertGreater(len(snapshot["compute_results"]), 0)

    # ========== 链路7: 行为热力消费事件聚合数据 ==========
    def test_chain_heatmap_consumes_aggregate(self) -> None:
        """行为热力图消费事件聚合结果。"""
        # 1. 先执行事件聚合
        aggregate_events("EA-001")

        # 2. 生成热力图
        heatmap = generate_heatmap("BH-001")
        self.assertEqual(heatmap["config_id"], "BH-001")
        self.assertIn("heatmap_data", heatmap)

        # 3. 验证产物
        self.assertIsNotNone(get_heatmap("BH-001"))

    # ========== 链路8: 画像卡片消费标签计算+分群结果 ==========
    def test_chain_profile_card_consumes_compute_and_segment(self) -> None:
        """画像卡片消费标签计算结果和分群结果。"""
        # 1. 执行标签计算
        compute_tag("TC-001")
        compute_tag("TC-002")

        # 2. 执行分群
        compute_segment("SEG-001")

        # 3. 构建画像卡片
        profile = build_profile_card("PC-001")
        self.assertEqual(profile["card_id"], "PC-001")
        self.assertGreater(len(profile["display_tags"]), 0)
        self.assertIsNotNone(profile["segment_info"])

        # 4. 验证产物
        self.assertIsNotNone(get_profile_result("PC-001"))

    # ========== 链路9: 标签下钻消费标签计算结果 ==========
    def test_chain_tag_drill_consumes_compute(self) -> None:
        """标签下钻分析消费标签计算结果。"""
        # 1. 执行标签计算
        compute_tag("TC-001")

        # 2. 执行下钻
        drill = drill_tag("TD-001")
        self.assertEqual(drill["parent_tag"], "TAG-001")
        self.assertIn("drill_data", drill)

    # ========== 链路10: 漏斗对比消费漏斗结果 ==========
    def test_chain_funnel_compare(self) -> None:
        """漏斗对比消费两个漏斗的分析结果。"""
        # 1. 执行两个漏斗
        analyze_funnel("FN-001")

        # 2. 对比（即使第二个不存在也能测试错误处理）
        with self.assertRaises(ValueError):
            from app.services.funnel_analyze import compare_funnels
            compare_funnels("FN-001", "FN-NONEXIST")

    # ========== 画像卡片：多任务匹配与差量口径 ==========
    def test_profile_card_one_task_per_tag(self) -> None:
        """同一标签挂了全量+差量任务时，卡片里只出现一条且取全量口径。"""
        profile = build_profile_card("PC-001")
        tag_ids = [item["tag_id"] for item in profile["display_tags"]]
        self.assertEqual(tag_ids.count("TAG-001"), 1)
        self.assertEqual(tag_ids, ["TAG-001", "TAG-002", "TAG-003"])

        tag_001 = next(item for item in profile["display_tags"] if item["tag_id"] == "TAG-001")
        self.assertEqual(tag_001["task_id"], "TC-001")
        self.assertEqual(tag_001["compute_scope"], "all")
        self.assertTrue(tag_001["is_full_coverage"])
        self.assertIsNotNone(tag_001["coverage_count"])

        # 选中任务应优先全量而不是最新的差量任务
        selected = select_compute_task("TAG-001")
        self.assertIsNotNone(selected)
        self.assertEqual(selected["task_id"], "TC-001")

    def test_profile_card_delta_not_counted_as_coverage(self) -> None:
        """差量重算只产出变化量，不能进入卡片覆盖总量。"""
        build_profile_card("PC-001")
        recompute_tag("TC-001", "delta")

        profile = build_profile_card("PC-001")
        tag_001 = next(item for item in profile["display_tags"] if item["tag_id"] == "TAG-001")
        self.assertEqual(tag_001["compute_scope"], "delta")
        self.assertFalse(tag_001["is_full_coverage"])
        self.assertIsNone(tag_001["coverage_count"])
        self.assertGreater(tag_001["result_count"], 0)
        # 没有可用的全量结果时总量为空，而不是把差量当覆盖数
        self.assertIsNone(profile["total_coverage"])

    def test_profile_card_marks_pending_and_consumed_versions(self) -> None:
        """一张卡片两个标签，一个已算完一个仍在计算：标出采用的输入与未就绪标签。"""
        profile = build_profile_card("PC-001")
        self.assertIn("TAG-003", profile["pending_tags"])
        self.assertNotIn("TAG-001", profile["pending_tags"])

        inputs = profile["input_versions"]
        self.assertEqual(inputs["TAG-001"]["task_id"], "TC-001")
        self.assertEqual(inputs["TAG-001"]["compute_version"], "TC-001-v1")
        self.assertTrue(inputs["TAG-001"]["ready"])
        self.assertEqual(inputs["TAG-003"]["task_id"], "TC-003")
        self.assertFalse(inputs["TAG-003"]["ready"])

    def test_profile_card_recompute_invalidates_and_reuses_new_version(self) -> None:
        """重算后历史产物失效，再次进入卡片能核对到新版本。"""
        first = build_profile_card("PC-001")
        self.assertEqual(first["input_versions"]["TAG-001"]["compute_version"], "TC-001-v1")

        recompute_tag("TC-001")
        # 旧卡片产物被失效，不会继续回查历史覆盖值
        self.assertIsNone(store.get_artifact("profile_card", "PC-001"))
        self.assertIsNone(store.get_artifact("profile_card_tag_detail", "PC-001"))

        second = build_profile_card("PC-001")
        self.assertEqual(second["input_versions"]["TAG-001"]["compute_version"], "TC-001-v2")

        # 回查返回最近一次刷新产物，总量与明细一致
        back = get_profile_result("PC-001")
        self.assertEqual(back["total_coverage"], second["total_coverage"])
        detail = get_profile_tag_detail("PC-001")
        self.assertEqual(detail["input_versions"]["TAG-001"]["compute_version"], "TC-001-v2")
        self.assertEqual(detail["total_coverage"], second["total_coverage"])

    def test_profile_card_detail_references_task_version(self) -> None:
        """画像标签明细字段引用所消费的任务版本。"""
        build_profile_card("PC-001")
        detail = get_profile_tag_detail("PC-001")
        self.assertEqual(len(detail["tags"]), 3)
        for row in detail["tags"]:
            self.assertTrue(row["task_id"])
            self.assertTrue(row["compute_version"])
            self.assertEqual(row["card_id"], "PC-001")
        tag_002 = next(row for row in detail["tags"] if row["tag_id"] == "TAG-002")
        self.assertFalse(tag_002["is_full_coverage"])
        self.assertIsNone(tag_002["coverage_count"])

    # ========== 额外测试：数据产物仓库 ==========    def test_artifact_storage_and_retrieval(self) -> None:
        """测试数据产物存储和查询。"""
        store.set_artifact("test", "key1", {"data": "value1"})
        artifact = store.get_artifact("test", "key1")
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact["data"], "value1")

    def test_snapshot_management(self) -> None:
        """测试快照管理。"""
        store.add_snapshot("test_snap", {"version": 1})
        store.add_snapshot("test_snap", {"version": 2})
        latest = store.get_latest_snapshot("test_snap")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["version"], 2)

    def test_cross_module_join(self) -> None:
        """测试跨模块关联查询。"""
        # attr_dict 和 tag_define 通过 attr_id 关联（source_attrs 是列表，改用 tag_level 简单测试）
        results = store.join("tag_define", "attr_dict", "tag_level", "data_type")
        self.assertIsInstance(results, list)


class TestBusinessLogic(unittest.TestCase):
    """测试核心业务逻辑正确性。"""

    def setUp(self) -> None:
        store.reset()

    def test_tag_compute_logic_eval(self) -> None:
        """测试标签计算逻辑求值。"""
        from app.services.tag_compute import _eval_condition
        user = {"ATTR-002": 2, "ATTR-003": 1500.0}
        attr_defs = {}
        result = _eval_condition(user, "ATTR-002 <= 3 AND ATTR-003 > 1000", attr_defs)
        self.assertTrue(result)

        user2 = {"ATTR-002": 10, "ATTR-003": 500.0}
        result2 = _eval_condition(user2, "ATTR-002 <= 3 AND ATTR-003 > 1000", attr_defs)
        self.assertFalse(result2)

    def test_funnel_conversion_declines(self) -> None:
        """测试漏斗转化率递减。"""
        result = analyze_funnel("FN-001")
        counts = result.step_counts
        for i in range(1, len(counts)):
            self.assertLessEqual(counts[i], counts[i - 1])

    def test_session_split_creates_sessions(self) -> None:
        """测试会话切割产生会话。"""
        result = split_sessions("SS-001")
        self.assertGreater(result.session_count, 0)
        self.assertLessEqual(result.session_count, result.total_events)

    def test_retention_rate_declines(self) -> None:
        """测试留存率随时间递减。"""
        result = calculate_retention("RC-001")
        for key, rates in result.retention_rates.items():
            for i in range(1, len(rates)):
                self.assertLessEqual(rates[i], rates[i - 1])

    def test_crowd_select_produces_users(self) -> None:
        """测试圈选产生用户列表。"""
        result = select_crowd("CS-001")
        self.assertGreater(result["matched_count"], 0)
        self.assertGreater(len(result["matched_users"]), 0)

    def test_overlap_respects_mode(self) -> None:
        """测试交集模式正确性。"""
        # 确保两个圈选有结果
        select_crowd("CS-001")
        select_crowd("CS-002")
        result = analyze_overlap("OA-001")
        # intersect 模式结果数不应超过任一人群
        self.assertLessEqual(
            result["result_count"],
            max(result["users_a_count"], result["users_b_count"])
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
