"""revisions 版本/差额验证（仅标准库）。运行：python3 -m unittest tests.test_revisions"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "maimai-mealcrew", "scripts"))

import settlement as S
import revisions as R


def _sett(subsidy):
    items = [
        {"item_id": "i1", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
         "bearer_type": "person", "bearer": "m1"},
        {"item_id": "i2", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
         "bearer_type": "person", "bearer": "m2"},
    ]
    return {
        "session_id": "t", "members": [{"id": "m1"}, {"id": "m2"}],
        "settlement_mode": "itemized", "public_fee_split": "equal",
        "items": items, "public_fees": {"delivery_cents": 0, "packaging_cents": 0, "service_cents": 0},
        "order_discount_cents": 0, "order_discount_scope": "ALL", "subsidy": subsidy,
    }


def _diff(old_s, new_s):
    old_q = S.build_demo_quote(old_s)
    new_q = S.build_demo_quote(new_s)
    old_set = S.compute_settlement(old_s, old_q)
    new_set = S.compute_settlement(new_s, new_q)
    return R.diff_versions(R.make_version("prev", old_s, old_q, old_set),
                           R.make_version("new", new_s, new_q, new_set))


class TestVersionDiff(unittest.TestCase):
    def test_same_sessions_no_reconfirm(self):
        s = _sett({"type": "none"})
        diff = _diff(s, s)
        self.assertFalse(diff["reconfirm_required"])

    def test_member_add_requires_reconfirm(self):
        old_s = _sett({"type": "none"})
        new_s = _sett({"type": "none"})
        new_s["members"] = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
        diff = _diff(old_s, new_s)
        self.assertTrue(diff["reconfirm_required"])
        self.assertIn("成员增减", diff["reconfirm_reasons"])

    def test_subsidy_change_requires_reconfirm(self):
        old_s = _sett({"type": "none"})
        new_s = _sett({"type": "per_person", "per_person_cents": 100, "eligible": "ALL"})
        diff = _diff(old_s, new_s)
        self.assertTrue(diff["reconfirm_required"])
        self.assertIn("补贴规则变化", diff["reconfirm_reasons"])

    def test_public_fee_split_change_requires_reconfirm(self):
        old_s = _sett({"type": "none"})
        new_s = _sett({"type": "none"})
        new_s["public_fee_split"] = "by_meal_cost"
        diff = _diff(old_s, new_s)
        self.assertTrue(diff["reconfirm_required"])
        self.assertIn("公共费用分摊方式变化", diff["reconfirm_reasons"])

    def test_settlement_mode_change_requires_reconfirm(self):
        old_s = _sett({"type": "none"})
        new_s = _sett({"type": "none"})
        new_s["settlement_mode"] = "equal_split"
        diff = _diff(old_s, new_s)
        self.assertTrue(diff["reconfirm_required"])
        self.assertIn("结算模式变化", diff["reconfirm_reasons"])

    def test_per_member_delta(self):
        old_s = _sett({"type": "none"})
        new_s = _sett({"type": "none"})
        new_s["items"].append({"item_id": "i3", "unit_price_cents": 500, "qty": 1,
                               "item_discount_cents": 0, "bearer_type": "person", "bearer": "m1"})
        diff = _diff(old_s, new_s)
        changed = [c for c in diff["per_member_changes"] if c["status"] == "changed"]
        self.assertTrue(any(c["member_id"] == "m1" for c in changed))


if __name__ == "__main__":
    unittest.main()
