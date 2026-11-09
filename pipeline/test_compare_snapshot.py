"""板块回归比对的单层口径测试（换源后无一级/二级之分）。"""
from __future__ import annotations

import copy
import unittest

from compare_snapshot import compare_sectors

ALGORITHM = "sector-donchian20-ma60-v2.0-em-source"

ITEMS = [
    {"code": "BK1283", "name": "银行", "date": "2026-11-04", "state": "bull",
     "strength": 14.8944, "days": 8, "group": "bull"},
    {"code": "BK0438", "name": "食品饮料", "date": "2026-11-04", "state": "bull",
     "strength": 2.5, "days": 3, "group": "bull"},
    {"code": "BK0433", "name": "农林牧渔", "date": "2026-11-04", "state": "bear",
     "strength": -31.2, "days": 12, "group": "bear"},
    {"code": "BK0728", "name": "环保", "date": "2026-11-04", "state": None,
     "strength": None, "days": None, "group": "unclassified"},
]


def _payload(as_of: str = "2026-11-04", items: list[dict] | None = None) -> dict:
    groups = {"bull": [], "warn": [], "bear": [], "rise": [], "unclassified": []}
    for item in (ITEMS if items is None else items):
        row = {k: v for k, v in item.items() if k != "group"}
        groups[item["group"]].append(row)
    return {"as_of": as_of, "data_source": "东财行业板块",
            "algorithm_version": ALGORITHM, "groups": groups}


class SectorCompareTest(unittest.TestCase):
    def test_identical_payload_passes(self):
        """同源同数据：现场重建与基线逐板块一致，不应报任何问题。"""
        self.assertEqual(compare_sectors(_payload(), _payload()), [])

    def test_as_of_mismatch_is_reported(self):
        problems = compare_sectors(_payload(as_of="2026-11-03"), _payload())
        self.assertEqual(len(problems), 1)
        self.assertIn("板块 as_of 不一致", problems[0])

    def test_state_strength_days_group_changes_are_all_reported(self):
        """单层后每个板块同级：状态、强度、天数、分组任一变化都必须判失败。"""
        for field, value, keyword in (
                ("state", "bear", "state 变化"),
                ("strength", -31.2, "strength 变化"),
                ("days", 99, "days 变化"),
                ("group", "bear", "分组变化")):
            current = copy.deepcopy(ITEMS)
            current[0][field] = value
            with self.subTest(field=field):
                problems = compare_sectors(_payload(), _payload(items=current))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(keyword, problems[0])
                self.assertIn("银行(BK1283)", problems[0])

    def test_strength_precision_within_four_decimals_passes(self):
        """产物按 4 位小数落盘，基线保留全精度时不应误报。"""
        current = copy.deepcopy(ITEMS)
        current[0]["strength"] = 14.89441
        self.assertEqual(compare_sectors(_payload(), _payload(items=current)), [])

    def test_missing_sector_stops_before_per_sector_diffs(self):
        current = copy.deepcopy(ITEMS)[1:]
        problems = compare_sectors(_payload(), _payload(items=current))
        self.assertEqual(len(problems), 1)
        self.assertIn("板块集合不一致", problems[0])
        self.assertIn("BK1283", problems[0])

    def test_duplicate_code_is_reported(self):
        """重复 code 会在 map 里静默覆盖，必须按条目数对账报出来。"""
        current = copy.deepcopy(ITEMS)
        current.append(dict(current[0]))  # 完全重复的一条（集合仍一致，只能靠条目数对账）
        problems = compare_sectors(_payload(), _payload(items=current))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("板块 code 重复", problems[0])
        self.assertIn("条目 5 个 / 唯一 code 4 个", problems[0])

    def test_unclassified_without_state_passes(self):
        """未分类板块 state 为空视为一致，不因为 None 派生逻辑误报。"""
        current = copy.deepcopy(ITEMS)
        current[3] = dict(current[3], state=None, strength=None, days=None)
        self.assertEqual(compare_sectors(_payload(), _payload(items=current)), [])

    def test_legacy_subsectors_are_ignored(self):
        """旧两层的 subsectors 不再进入回归口径：只看 31 个行业板块自身那条记录。"""
        baseline = _payload()
        current = _payload()
        current["groups"]["bull"][0]["subsectors"] = [
            {"code": "BK9999", "name": "旧二级板块", "state": "bear", "strength": -1.0, "days": 2}]
        self.assertEqual(compare_sectors(baseline, current), [])


if __name__ == "__main__":
    unittest.main()
