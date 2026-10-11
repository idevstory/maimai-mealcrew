"""render 渲染验证：HTML 转义、来源标识（仅标准库）。运行：python3 -m unittest tests.test_render"""

import os
import sys
import unittest
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "maimai-mealcrew", "scripts"))

import settlement as S
import render_report as RR

EX = os.path.join(ROOT, "skill", "maimai-mealcrew", "examples", "demo_session.json")


def _base_session():
    with open(EX, encoding="utf-8") as f:
        return json.load(f)


class TestHtmlEscape(unittest.TestCase):
    def test_script_in_name_is_escaped(self):
        sess = _base_session()
        sess = json.loads(json.dumps(sess))
        sess["members"][0]["name"] = "<script>alert(1)</script>"
        q = S.build_demo_quote(sess)
        res = S.compute_settlement(sess, q)
        html = RR.render_html(res)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_fallback_escapes_json(self):
        res = S.compute_settlement(_base_session(), S.build_demo_quote(_base_session()))
        html = RR.render_html(res, template_path="/nonexistent/template.html")
        # 即便模板缺失，也不应原样出现可破坏结构的标记
        self.assertIn("<pre>", html)


class TestSourceLabel(unittest.TestCase):
    def test_demo_label(self):
        sess = _base_session()
        res = S.compute_settlement(sess, S.build_demo_quote(sess))
        md = RR.render_markdown(res)
        html = RR.render_html(res)
        self.assertIn("演示应付", md)
        self.assertIn("演示数据", html)
        self.assertIn("source-badge demo", html)

    def test_official_label(self):
        sess = _base_session()
        q = {"official_payable_cents": 19500, "source": "mcp"}
        res = S.compute_settlement(sess, q)
        md = RR.render_markdown(res)
        html = RR.render_html(res)
        self.assertIn("官方应付", md)
        self.assertIn("来源：官方实时核价", html)
        self.assertIn("source-badge official", html)

    def test_result_carries_source(self):
        sess = _base_session()
        res = S.compute_settlement(sess, S.build_demo_quote(sess))
        self.assertEqual(res["quote_source"], "demo")


class TestCliBlocking(unittest.TestCase):
    def _write_tmp(self, obj):
        p = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(obj, p, ensure_ascii=False)
        p.close()
        return p.name

    def test_settle_real_without_quote_rejected(self):
        import mealcrew
        sess_p = self._write_tmp(_base_session())
        rc = mealcrew.main(["settle", "--session", sess_p, "--mode", "real", "--render", "json"])
        self.assertEqual(rc, 2)

    def test_settle_conservation_mismatch_blocked(self):
        import mealcrew
        sess = _base_session()
        sess = json.loads(json.dumps(sess))
        # 让本地计算口径（商品+费用）与官方报价不一致
        sess["items"] = [{"item_id": "i1", "unit_price_cents": 3000, "qty": 1,
                          "item_discount_cents": 0, "bearer_type": "person", "bearer": "m1"}]
        sess["members"] = [{"id": "m1"}]
        sess["subsidy"] = {"type": "none"}
        sess["order_discount_cents"] = 0
        sess_p = self._write_tmp(sess)
        quote_p = self._write_tmp({"official_payable_cents": 900, "source": "mcp"})
        out_p = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False).name
        rc = mealcrew.main(["settle", "--session", sess_p, "--quote", quote_p,
                            "--mode", "real", "--render", "html", "--output", out_p])
        self.assertEqual(rc, 1)
        self.assertEqual(os.path.getsize(out_p), 0)  # 未生成正式账单

    def test_revise_real_without_quotes_rejected(self):
        import mealcrew
        sess_p = self._write_tmp(_base_session())
        rc = mealcrew.main(["revise", "--session", sess_p, "--changes", "{}", "--mode", "real"])
        self.assertEqual(rc, 2)

    def test_revise_resets_state(self):
        import mealcrew
        sess = _base_session()
        sess = json.loads(json.dumps(sess))
        sess["state"] = "CONFIRMED"
        sess["confirmation"] = {"by": "m1", "at": "2026-10-11T00:00:00Z"}
        sess_p = self._write_tmp(sess)
        out_p = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
        rc = mealcrew.main(["revise", "--session", sess_p, "--changes", "{}",
                            "--mode", "demo", "--output", out_p])
        self.assertEqual(rc, 0)
        with open(out_p, encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual(out["new_session"]["state"], "DRAFT")
        self.assertNotIn("confirmation", out["new_session"])


if __name__ == "__main__":
    unittest.main()
