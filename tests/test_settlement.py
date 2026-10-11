"""settlement 业务规则与异常验证（仅标准库）。运行：python3 -m unittest tests.test_settlement"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "maimai-mealcrew", "scripts"))

import settlement as S


def _session(mode="itemized", items=None, fees=None, order_disc=0, subsidy=None, members=None):
    return {
        "session_id": "t",
        "members": members or [{"id": "m1", "name": "A"}, {"id": "m2", "name": "B"}, {"id": "m3", "name": "C"}],
        "settlement_mode": mode,
        "public_fee_split": "equal",
        "items": items or [],
        "public_fees": fees or {"delivery_cents": 0, "packaging_cents": 0, "service_cents": 0},
        "order_discount_cents": order_disc,
        "order_discount_scope": "ALL",
        "subsidy": subsidy or {"type": "none"},
    }


def _quote(official):
    return {"official_payable_cents": official, "source": "mcp"}


class TestAllocate(unittest.TestCase):
    def test_split_equally_sums(self):
        self.assertEqual(sum(S.split_equally(100, 3)), 100)
        self.assertEqual(S.split_equally(100, 3), [34, 33, 33])

    def test_proportional_sums_and_deterministic(self):
        self.assertEqual(sum(S.allocate_proportional(10, [1, 2, 3])), 10)
        self.assertEqual(S.allocate_proportional(10, [1, 2, 3]), [2, 3, 5])
        # 相同输入应得相同结果
        self.assertEqual(S.allocate_proportional(100, [1, 1, 1]), [34, 33, 33])
        # 零总额
        self.assertEqual(S.allocate_proportional(0, [1, 2, 3]), [0, 0, 0])
        # 权重全 0 退化为均分
        self.assertEqual(sum(S.allocate_proportional(9, [0, 0, 0])), 9)


class TestItemizedConservation(unittest.TestCase):
    def test_itemized_and_shared_conservation(self):
        items = [
            {"item_id": "i1", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m1"},
            {"item_id": "i2", "unit_price_cents": 2000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m2"},
            {"item_id": "i3", "unit_price_cents": 3000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "shared", "shared_members": ["m1", "m2", "m3"]},
        ]
        sess = _session(items=items, fees={"delivery_cents": 300, "packaging_cents": 0, "service_cents": 0})
        # 官方应付 = 6000 + 300 = 6300
        res = S.compute_settlement(sess, _quote(6300))
        self.assertTrue(res["conservation"]["sum_personal_plus_company_equals_official"])
        self.assertTrue(res["reconciliation"]["ok"])
        self.assertEqual(res["official_payable_cents"], 6300)
        # 餐费归属：m1=1000+1000=2000, m2=2000+1000=3000, m3=0+1000=1000
        by = {m["id"]: m for m in res["members"]}
        self.assertEqual(by["m1"]["meal_cost_cents"], 2000)
        self.assertEqual(by["m2"]["meal_cost_cents"], 3000)
        self.assertEqual(by["m3"]["meal_cost_cents"], 1000)
        # 费用均分 100/人
        self.assertEqual(by["m1"]["fee_share_cents"], 100)


class TestSubsidy(unittest.TestCase):
    def test_per_person_capped_no_negative(self):
        items = [
            {"item_id": "i1", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m1"},
            {"item_id": "i2", "unit_price_cents": 2000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m2"},
            {"item_id": "i3", "unit_price_cents": 3000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "shared", "shared_members": ["m1", "m2", "m3"]},
        ]
        fees = {"delivery_cents": 300, "packaging_cents": 0, "service_cents": 0}
        subsidy = {"type": "per_person", "per_person_cents": 2500, "eligible": "ALL"}
        sess = _session(items=items, fees=fees, subsidy=subsidy)
        res = S.compute_settlement(sess, _quote(6300))
        by = {m["id"]: m for m in res["members"]}
        # m1 应担 2100，补贴封顶为 2100，个人自付 0（不为负）
        self.assertEqual(by["m1"]["company_bears_cents"], 2100)
        self.assertEqual(by["m1"]["personal_pays_cents"], 0)
        self.assertTrue(all(m["personal_pays_cents"] >= 0 for m in res["members"]))
        self.assertTrue(res["conservation"]["sum_personal_plus_company_equals_official"])

    def test_ineligible_member_no_subsidy(self):
        items = [
            {"item_id": "i1", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m1"},
            {"item_id": "i2", "unit_price_cents": 2000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m2"},
            {"item_id": "i3", "unit_price_cents": 3000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "shared", "shared_members": ["m1", "m2", "m3"]},
        ]
        subsidy = {"type": "per_person", "per_person_cents": 2500, "eligible": ["m1"]}
        sess = _session(items=items, fees={"delivery_cents": 300, "packaging_cents": 0, "service_cents": 0}, subsidy=subsidy)
        res = S.compute_settlement(sess, _quote(6300))
        by = {m["id"]: m for m in res["members"]}
        self.assertEqual(by["m2"]["company_bears_cents"], 0)
        self.assertEqual(by["m3"]["company_bears_cents"], 0)
        self.assertEqual(by["m1"]["company_bears_cents"], 2100)
        self.assertTrue(res["conservation"]["sum_personal_plus_company_equals_official"])

    def test_whole_order_capped(self):
        sess = _session(items=[
            {"item_id": "i1", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m1"},
        ], subsidy={"type": "whole_order", "whole_order_cents": 999999, "eligible": "ALL"})
        # 单商品 1000 + 0 费 = 1000 应付；公司补贴不超过 1000
        res = S.compute_settlement(sess, _quote(1000))
        total_company = sum(m["company_bears_cents"] for m in res["members"])
        self.assertEqual(total_company, 1000)
        self.assertTrue(all(m["personal_pays_cents"] >= 0 for m in res["members"]))


class TestOrderDiscount(unittest.TestCase):
    def test_order_discount_allocated_and_conserved(self):
        items = [
            {"item_id": "i1", "unit_price_cents": 2000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m1"},
            {"item_id": "i2", "unit_price_cents": 2000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m2"},
        ]
        # 满减 1000 适用 ALL
        sess = _session(items=items, order_disc=1000)
        # 应付 = 4000 - 1000 = 3000
        res = S.compute_settlement(sess, _quote(3000))
        self.assertTrue(res["conservation"]["sum_personal_plus_company_equals_official"])
        self.assertTrue(res["reconciliation"]["ok"])
        # 优惠归属合计 == 1000
        self.assertEqual(sum(m["discount_alloc_cents"] for m in res["members"]), 1000)

    def test_scope_limits_order_discount(self):
        items = [
            {"item_id": "i1", "unit_price_cents": 3000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m1"},
            {"item_id": "i2", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m2"},
        ]
        sess = _session(items=items, order_disc=800)
        sess["order_discount_scope"] = ["i1"]
        res = S.compute_settlement(sess, _quote(3200))  # 应付=3200
        self.assertTrue(res["reconciliation"]["ok"])
        self.assertTrue(res["conservation"]["sum_personal_plus_company_equals_official"])
        by = {m["id"]: m for m in res["members"]}
        # 优惠只分配到 m1
        self.assertEqual(by["m1"]["discount_alloc_cents"], 800)
        self.assertEqual(by["m2"]["discount_alloc_cents"], 0)


class TestEqualSplit(unittest.TestCase):
    def test_equal_split_conservation(self):
        sess = _session(mode="equal_split", items=[
            {"item_id": "i1", "unit_price_cents": 1234, "qty": 1},
        ])
        res = S.compute_settlement(sess, _quote(1234))
        self.assertEqual(sum(m["personal_pays_cents"] for m in res["members"]), 1234)
        self.assertTrue(res["conservation"]["sum_personal_plus_company_equals_official"])


class TestCoversPublicFees(unittest.TestCase):
    def _base(self, covers):
        items = [
            {"item_id": "i1", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m1"},
            {"item_id": "i2", "unit_price_cents": 2000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m2"},
        ]
        fees = {"delivery_cents": 300, "packaging_cents": 0, "service_cents": 0}
        subsidy = {"type": "per_person", "per_person_cents": 2000, "eligible": "ALL",
                   "covers_public_fees": covers}
        return _session(items=items, fees=fees, subsidy=subsidy,
                        members=[{"id": "m1"}, {"id": "m2"}])

    def test_false_excludes_fee(self):
        res = S.compute_settlement(self._base(False), _quote(3300))
        by = {m["id"]: m for m in res["members"]}
        # 补贴只覆盖餐费：m1 餐费 1000 -> 公司 1000，个人 = 餐费1000+费150-1000=150
        self.assertEqual(by["m1"]["company_bears_cents"], 1000)
        self.assertEqual(by["m1"]["personal_pays_cents"], 150)
        # m2 餐费 2000 -> 公司 2000，个人 = 2000+150-2000=150
        self.assertEqual(by["m2"]["company_bears_cents"], 2000)
        self.assertEqual(by["m2"]["personal_pays_cents"], 150)
        # 个人自付永不为负，且守恒
        self.assertTrue(all(m["personal_pays_cents"] >= 0 for m in res["members"]))
        self.assertTrue(res["conservation"]["sum_personal_plus_company_equals_official"])

    def test_true_includes_fee(self):
        res = S.compute_settlement(self._base(True), _quote(3300))
        by = {m["id"]: m for m in res["members"]}
        # 补贴覆盖餐费+费用：m1 应担 1150 -> 公司 1150，个人 0
        self.assertEqual(by["m1"]["company_bears_cents"], 1150)
        self.assertEqual(by["m1"]["personal_pays_cents"], 0)
        self.assertTrue(all(m["personal_pays_cents"] >= 0 for m in res["members"]))
        self.assertTrue(res["conservation"]["sum_personal_plus_company_equals_official"])


class TestScopeStrictness(unittest.TestCase):
    def test_unknown_explicit_scope_raises(self):
        items = [
            {"item_id": "i1", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m1"},
            {"item_id": "i2", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m2"},
        ]
        sess = _session(items=items, order_disc=500)
        sess["order_discount_scope"] = ["zzz"]  # 未知商品
        with self.assertRaises(ValueError):
            S.compute_settlement(sess, _quote(1500))

    def test_zero_net_explicit_scope_raises(self):
        items = [
            {"item_id": "i1", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 1000,
             "bearer_type": "person", "bearer": "m1"},
            {"item_id": "i2", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m2"},
        ]
        sess = _session(items=items, order_disc=500)
        sess["order_discount_scope"] = ["i1"]  # 适用范围内净额为 0
        with self.assertRaises(ValueError):
            S.compute_settlement(sess, _quote(1000))

    def test_valid_explicit_scope_kept(self):
        items = [
            {"item_id": "i1", "unit_price_cents": 3000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m1"},
            {"item_id": "i2", "unit_price_cents": 1000, "qty": 1, "item_discount_cents": 0,
             "bearer_type": "person", "bearer": "m2"},
        ]
        sess = _session(items=items, order_disc=800)
        sess["order_discount_scope"] = ["i1"]
        res = S.compute_settlement(sess, _quote(3200))
        by = {m["id"]: m for m in res["members"]}
        self.assertEqual(by["m1"]["discount_alloc_cents"], 800)
        self.assertEqual(by["m2"]["discount_alloc_cents"], 0)


class TestValidation(unittest.TestCase):
    def test_dup_member_rejected(self):
        with self.assertRaises(ValueError):
            S.validate_session(_session(members=[{"id": "m1"}, {"id": "m1"}]))

    def test_unknown_bearer_rejected(self):
        items = [{"item_id": "i1", "unit_price_cents": 1000, "qty": 1,
                  "bearer_type": "person", "bearer": "X"}]
        with self.assertRaises(ValueError):
            S.validate_session(_session(items=items))

    def test_zero_qty_rejected(self):
        items = [{"item_id": "i1", "unit_price_cents": 1000, "qty": 0,
                  "bearer_type": "person", "bearer": "m1"}]
        with self.assertRaises(ValueError):
            S.validate_session(_session(items=items))

    def test_demo_session_valid(self):
        import json
        ex_path = os.path.join(ROOT, "skill", "maimai-mealcrew", "examples", "demo_session.json")
        with open(ex_path, encoding="utf-8") as f:
            S.validate_session(json.load(f))


class TestDemoQuote(unittest.TestCase):
    def test_demo_quote_conservation_with_example(self):
        import json
        ex_path = os.path.join(ROOT, "skill", "maimai-mealcrew", "examples", "demo_session.json")
        with open(ex_path, encoding="utf-8") as f:
            sess = json.load(f)
        q = S.build_demo_quote(sess)
        res = S.compute_settlement(sess, q)
        self.assertTrue(res["conservation"]["sum_personal_plus_company_equals_official"])
        self.assertTrue(res["reconciliation"]["ok"])


if __name__ == "__main__":
    unittest.main()
