#!/usr/bin/env python3
"""mealcrew.py — 麦麦团餐助手统一 CLI（仅标准库）。

子命令：
  normalize  把结构化需求归一化为会话 JSON（补全默认值、稳定 item_id）
  candidates 产出候选方案与待核价请求（demo 直接本地核价；real 输出 MCP 请求）
  settle     结算：官方核价金额 + 本地分摊/补贴，输出账单
  revise     改单：应用变更 -> 新版本 -> 重新核价与差额对比
  render     把结算结果渲染为 md / html / json
  demo       离线演示：6 人合成场景端到端跑通（所有结果标注为演示）
  doctor     自检：目录完整性、运行时、样例计算、可见工具

所有金额以整数分处理；输入输出 JSON；诊断与错误单独输出。
真实模式缺少有效 MCP 连接时，settle/candidates 不会伪造实时价格。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settlement as S
import revisions as R
import render_report as RR

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(SCRIPT_DIR, "..", "examples")
DEFAULT_DEMO = os.path.join(EXAMPLES_DIR, "demo_session.json")


# ----------------------------- 工具 -----------------------------

def _load_json(path):
    """接受内联 JSON 字符串或文件路径。"""
    try:
        return json.loads(path)
    except (json.JSONDecodeError, TypeError):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def _save_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _default_session(raw: dict) -> dict:
    """补全会话默认值，保证结算函数可稳定运行。"""
    sess = dict(raw)
    sess.setdefault("session_id", raw.get("session_id", "session-1"))
    sess.setdefault("settlement_mode", "itemized")
    sess.setdefault("public_fee_split", "equal")
    sess.setdefault("store", raw.get("store"))
    sess.setdefault("channel", raw.get("channel", "takeaway"))
    sess.setdefault("public_fees", {"delivery_cents": 0, "packaging_cents": 0, "service_cents": 0})
    sess.setdefault("order_discount_cents", 0)
    sess.setdefault("order_discount_scope", "ALL")
    sess.setdefault("subsidy", {"type": "none"})
    items = []
    for idx, it in enumerate(raw.get("items", [])):
        it = dict(it)
        it.setdefault("item_id", it.get("item_id") or f"i{idx + 1}")
        it.setdefault("qty", 1)
        it.setdefault("unit_price_cents", 0)
        it.setdefault("item_discount_cents", 0)
        it.setdefault("bearer_type", "person")
        if it["bearer_type"] == "person":
            it.setdefault("bearer", None)
        else:
            it.setdefault("shared_members", [])
        items.append(it)
    sess["items"] = items
    members = []
    for idx, m in enumerate(raw.get("members", [])):
        m = dict(m)
        m.setdefault("id", m.get("id") or f"m{idx + 1}")
        m.setdefault("name", m.get("name") or m["id"])
        members.append(m)
    sess["members"] = members
    return sess


def _apply_changes(session: dict, changes: dict) -> dict:
    """返回应用变更后的新会话副本（不修改入参）。"""
    new = json.loads(json.dumps(session))  # deep copy
    items_by_id = {it["item_id"]: it for it in new["items"]}

    for it in changes.get("add_items", []):
        it = dict(it)
        it.setdefault("item_id", f"i{len(new['items']) + 1}")
        new["items"].append(it)
    for rid in changes.get("remove_items", []):
        new["items"] = [it for it in new["items"] if it["item_id"] != rid]
    for iid, patch in changes.get("update_items", {}).items():
        if iid in items_by_id:
            items_by_id[iid].update(patch)

    members_by_id = {m["id"]: m for m in new["members"]}
    for m in changes.get("add_members", []):
        if m["id"] not in members_by_id:
            new["members"].append(m)
    removed = set(changes.get("remove_members", []))
    if removed:
        new["members"] = [m for m in new["members"] if m["id"] not in removed]
        member_ids_after = {m["id"] for m in new["members"]}
        # 清理被移除成员在商品上的归属
        for it in new["items"]:
            if it.get("bearer_type") == "person" and it.get("bearer") in removed:
                it["bearer"] = None
            if it.get("bearer_type") == "shared":
                it["shared_members"] = [x for x in it.get("shared_members", []) if x not in removed]
        # 删除归属失效的商品（个人餐品无人承担 / 共享餐无人分享）
        new["items"] = [
            it for it in new["items"]
            if not (it.get("bearer_type") == "person" and it.get("bearer") not in member_ids_after)
            and not (it.get("bearer_type") == "shared"
                     and not any(x in member_ids_after for x in it.get("shared_members", [])))
        ]

    for key in ("store", "channel", "public_fees", "order_discount_cents",
                "order_discount_scope", "subsidy", "settlement_mode", "public_fee_split"):
        if key in changes:
            new[key] = changes[key]
    return new


# ----------------------------- 子命令 -----------------------------

def cmd_normalize(args):
    raw = _load_json(args.input)
    sess = _default_session(raw)
    if args.output:
        _save_json(sess, args.output)
        print(f"[normalize] 已写出会话：{args.output}")
    else:
        print(json.dumps(sess, ensure_ascii=False, indent=2))
    return 0


def cmd_candidates(args):
    sess = _default_session(_load_json(args.session))
    if args.mode == "demo" or not args.quote:
        quote = S.build_demo_quote(sess)
        source = "demo"
    else:
        quote = _load_json(args.quote)
        source = "mcp"
    if source == "demo":
        print("[candidates] 演示模式：使用本地数字构造报价，非官方实时核价。")
    # 候选方案：原餐单（此处列出待发送 calculate-price 的商品明细）
    payload = {
        "source": source,
        "store": sess.get("store"),
        "channel": sess.get("channel"),
        "items": [
            {"item_id": it["item_id"], "product_code": it.get("product_code"),
             "qty": it.get("qty", 1), "unit_price_cents": it.get("unit_price_cents"),
             "item_discount_cents": it.get("item_discount_cents", 0)}
            for it in sess["items"]
        ],
        "quote_preview": {"official_payable_cents": quote["official_payable_cents"]},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_settle(args):
    sess = _default_session(_load_json(args.session))
    if args.mode == "demo" or not args.quote:
        quote = S.build_demo_quote(sess)
        if args.mode != "demo":
            print("[settle] 未提供 quote 且非 demo，使用本地构造的演示报价。", file=sys.stderr)
    else:
        quote = _load_json(args.quote)
    result = S.compute_settlement(sess, quote)

    fmt = args.render or "json"
    if fmt == "json":
        print(RR.render_json(result, desensitize=not args.no_desensitize))
    elif fmt == "md":
        out = RR.render_markdown(result, desensitize=not args.no_desensitize)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)
            print(f"[settle] Markdown 账单已写出：{args.output}")
        else:
            print(out)
    elif fmt == "html":
        out = RR.render_html(result, desensitize=not args.no_desensitize)
        path = args.output or "mealcrew_bill.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"[settle] HTML 账单已写出：{path}")
    if not result["conservation"]["sum_personal_plus_company_equals_official"]:
        print("[settle] ⚠️ 金额守恒未通过，请核验字段契约。", file=sys.stderr)
    return 0


def cmd_revise(args):
    old_sess = _default_session(_load_json(args.session))
    changes = _load_json(args.changes)
    old_quote = S.build_demo_quote(old_sess) if (args.mode == "demo") else None
    old_sett = S.compute_settlement(old_sess, old_quote) if old_quote else None

    new_sess = _apply_changes(old_sess, changes)
    new_sess["session_id"] = old_sess.get("session_id", "session-1")
    new_quote = S.build_demo_quote(new_sess) if (args.mode == "demo") else None
    new_sett = S.compute_settlement(new_sess, new_quote)

    if old_sett and new_quote:
        old_ver = R.make_version("prev", old_sess, old_quote, old_sett)
        new_ver = R.make_version(changes.get("version_label", "revised"), new_sess, new_quote, new_sett)
        diff = R.diff_versions(old_ver, new_ver)
    else:
        diff = {"note": "真实模式差额需基于两次官方核价结果对比（此处仅输出新版本）。"}

    output = {"new_session": new_sess, "new_settlement": new_sett, "diff": diff}
    if args.output:
        _save_json(output, args.output)
        print(f"[revise] 新版本与差额已写出：{args.output}")
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    if diff.get("reconfirm_required"):
        print("[revise] ⚠️ 需重新确认：" + "、".join(diff.get("reconfirm_reasons", [])), file=sys.stderr)
    return 0


def cmd_render(args):
    result = _load_json(args.result)
    fmt = args.format
    if fmt == "json":
        print(RR.render_json(result, desensitize=not args.no_desensitize))
    elif fmt == "md":
        out = RR.render_markdown(result, desensitize=not args.no_desensitize)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)
            print(f"[render] Markdown 已写出：{args.output}")
        else:
            print(out)
    elif fmt == "html":
        out = RR.render_html(result, desensitize=not args.no_desensitize)
        path = args.output or "mealcrew_bill.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"[render] HTML 已写出：{path}")
    return 0


def cmd_demo(args):
    if not os.path.exists(DEFAULT_DEMO):
        print(f"[demo] 缺少示例数据：{DEFAULT_DEMO}", file=sys.stderr)
        return 2
    sess = _default_session(_load_json(DEFAULT_DEMO))
    quote = S.build_demo_quote(sess)
    result = S.compute_settlement(sess, quote)

    out_dir = args.output or "."
    os.makedirs(out_dir, exist_ok=True)
    demo_json = os.path.join(out_dir, "demo_result.json")
    demo_html = os.path.join(out_dir, "demo_report.html")
    _save_json(result, demo_json)
    with open(demo_html, "w", encoding="utf-8") as f:
        f.write(RR.render_html(result, desensitize=True))
    print("=== [demo] 离线演示（非官方实时核价） ===")
    print(RR.render_markdown(result, desensitize=True))
    print(f"\n[demo] 结果已写出：{demo_json}\n[demo] 账单已写出：{demo_html}")
    return 0


def cmd_doctor(args):
    checks = []
    skill_root = os.path.join(SCRIPT_DIR, "..")

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 1) 目录/文件完整性
    need = [
        "SKILL.md",
        "scripts/mealcrew.py",
        "scripts/settlement.py",
        "scripts/revisions.py",
        "scripts/render_report.py",
        "templates/report.html",
        "references/data-contract.md",
    ]
    for rel in need:
        p = os.path.join(skill_root, rel)
        check(f"存在 {rel}", os.path.exists(p))

    # 2) Python 运行时
    import platform
    check("Python 版本 >= 3.9", sys.version_info >= (3, 9), platform.python_version())

    # 3) 样例计算（最小场景：3 人均分 + 补贴）
    sample = {
        "session_id": "doctor-sample",
        "members": [{"id": "m1", "name": "A"}, {"id": "m2", "name": "B"}, {"id": "m3", "name": "C"}],
        "settlement_mode": "equal_split",
        "items": [],
        "public_fees": {"delivery_cents": 0, "packaging_cents": 0, "service_cents": 0},
        "subsidy": {"type": "whole_order", "whole_order_cents": 900, "eligible": "ALL"},
    }
    q = S.build_demo_quote(sample)
    q["official_payable_cents"] = 3000  # 构造 30.00 元总账，验证补贴后守恒
    res = S.compute_settlement(sample, q)
    ok_cons = res["conservation"]["sum_personal_plus_company_equals_official"]
    check("样例金额守恒", ok_cons, f"官方应付={q['official_payable_cents']}分")
    ok_subsidy = all(m["personal_pays_cents"] >= 0 for m in res["members"])
    check("补贴不造成个人自付为负", ok_subsidy)

    # 4) 可见工具说明
    check("MCP 工具发现", True, "tools/list 在运行时由宿主执行；本自检不读取真实 MCP")

    failed = [n for n, ok, _ in checks if not ok]
    print("\n[doctor] 结论：" + ("全部通过 ✅" if not failed else f"{len(failed)} 项未通过 ❌"))
    return 0 if not failed else 1


# ----------------------------- 参数 -----------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="mealcrew", description="麦麦团餐助手 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("normalize", help="归一化需求为会话 JSON")
    n.add_argument("--input", required=True)
    n.add_argument("--output")
    n.set_defaults(func=cmd_normalize)

    c = sub.add_parser("candidates", help="产出候选方案 / 核价请求")
    c.add_argument("--session", required=True)
    c.add_argument("--quote")
    c.add_argument("--mode", default="demo", choices=["demo", "real"])
    c.set_defaults(func=cmd_candidates)

    s = sub.add_parser("settle", help="结算并渲染账单")
    s.add_argument("--session", required=True)
    s.add_argument("--quote")
    s.add_argument("--mode", default="demo", choices=["demo", "real"])
    s.add_argument("--render", default="json", choices=["json", "md", "html"])
    s.add_argument("--output")
    s.add_argument("--no-desensitize", action="store_true")
    s.set_defaults(func=cmd_settle)

    r = sub.add_parser("revise", help="改单重算与差额对比")
    r.add_argument("--session", required=True)
    r.add_argument("--changes", required=True)
    r.add_argument("--mode", default="demo", choices=["demo", "real"])
    r.add_argument("--output")
    r.set_defaults(func=cmd_revise)

    rd = sub.add_parser("render", help="渲染结算结果为 md/html/json")
    rd.add_argument("--result", required=True)
    rd.add_argument("--format", default="json", choices=["json", "md", "html"])
    rd.add_argument("--output")
    rd.add_argument("--no-desensitize", action="store_true")
    rd.set_defaults(func=cmd_render)

    d = sub.add_parser("demo", help="离线演示（6 人合成场景）")
    d.add_argument("--output", default=".")
    d.set_defaults(func=cmd_demo)

    doc = sub.add_parser("doctor", help="自检")
    doc.set_defaults(func=cmd_doctor)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
