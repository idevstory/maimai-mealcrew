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
    if not isinstance(changes, dict):
        raise ValueError("changes 必须为对象")
    new = json.loads(json.dumps(session))  # deep copy
    items_by_id = {it["item_id"]: it for it in new["items"]}

    for it in changes.get("add_items", []):
        it = dict(it)
        if not it.get("item_id"):
            index = 1
            used_ids = {row["item_id"] for row in new["items"]}
            while f"i{index}" in used_ids:
                index += 1
            it["item_id"] = f"i{index}"
        new["items"].append(it)
    for rid in changes.get("remove_items", []):
        new["items"] = [it for it in new["items"] if it["item_id"] != rid]
    for iid, patch in changes.get("update_items", {}).items():
        if iid not in items_by_id:
            raise ValueError(f"更新商品不存在: {iid}")
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

    for key in ("store", "channel", "coupon_ids", "public_fees", "order_discount_cents",
                "order_discount_scope", "subsidy", "settlement_mode", "public_fee_split"):
        if key in changes:
            new[key] = changes[key]
    # 任意变更生成新版本：清除旧确认、状态回到 DRAFT（需重新确认）
    new.pop("confirmation", None)
    new.pop("confirmed_at", None)
    new["revision_number"] = session.get("revision_number", 0) + 1
    new.pop("quote", None)
    new["state"] = "DRAFT"
    return new


# ----------------------------- 子命令 -----------------------------

def cmd_normalize(args):
    raw = _load_json(args.input)
    sess = _default_session(raw)
    S.validate_session(sess)
    if args.output:
        _save_json(sess, args.output)
        print(f"[normalize] 已写出会话：{args.output}")
    else:
        print(json.dumps(sess, ensure_ascii=False, indent=2))
    return 0


def cmd_candidates(args):
    sess = _default_session(_load_json(args.session))
    S.validate_session(sess)
    if args.mode == "demo":
        quote = S.build_demo_quote(sess)
        source = "demo"
        print("[candidates] 演示模式：使用本地数字构造报价，非官方实时核价。", file=sys.stderr)
    elif args.quote:
        quote = _load_json(args.quote)
        S.validate_quote(sess, quote, real=True)
        S.require_valid_settlement(S.compute_settlement(sess, quote))
        source = quote["source"]
    else:
        # 真实模式未提供报价：仅产出待核价请求，不伪造本地金额
        payload = S.quote_request(sess)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
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
    S.validate_session(sess)
    if args.mode == "real" and not args.quote:
        print("[settle] 真实模式必须提供官方核价 --quote，未提供则拒绝生成账单（不退回演示冒充）。", file=sys.stderr)
        return 2
    if args.mode == "demo":
        quote = S.build_demo_quote(sess)
    else:
        quote = _load_json(args.quote)
        S.validate_quote(sess, quote, real=True)

    try:
        result = S.compute_settlement(sess, quote)
        S.require_valid_settlement(result)
    except ValueError as e:
        print(f"[settle] 结算被阻止：{e}", file=sys.stderr)
        return 1

    fmt = args.render or "json"
    if fmt == "json":
        out = RR.render_json(result, desensitize=not args.no_desensitize)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)
        else:
            print(out)
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
    return 0


def cmd_revise(args):
    old_sess = _default_session(_load_json(args.session))
    changes = _load_json(args.changes)
    new_sess = _default_session(_apply_changes(old_sess, changes))
    S.validate_session(new_sess)
    if args.prepare:
        output = {"new_session": new_sess, "quote_request": S.quote_request(new_sess)}
        if args.output:
            _save_json(output, args.output)
        else:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    if args.mode == "demo":
        old_quote = S.build_demo_quote(old_sess)
        new_quote = None  # 在应用变更后按新会话构造
    elif args.quote_old and args.quote_new:
        old_quote = _load_json(args.quote_old)
        new_quote = _load_json(args.quote_new)
    else:
        print("[revise] 真实模式必须同时提供 --quote-old 与 --quote-new（两次官方核价），"
              "旧报价不可用于新商品，故拒绝改单。", file=sys.stderr)
        return 2

    if args.mode == "demo":
        new_quote = S.build_demo_quote(new_sess)

    try:
        S.validate_quote(old_sess, old_quote, real=args.mode == "real")
        S.validate_quote(new_sess, new_quote, real=args.mode == "real")
        old_sett = S.compute_settlement(old_sess, old_quote)
        new_sett = S.compute_settlement(new_sess, new_quote)
        S.require_valid_settlement(old_sett)
        S.require_valid_settlement(new_sett)
    except ValueError as e:
        print(f"[revise] 改单被阻止：{e}", file=sys.stderr)
        return 1

    old_ver = R.make_version("prev", old_sess, old_quote, old_sett)
    new_ver = R.make_version(changes.get("version_label", "revised"), new_sess, new_quote, new_sett)
    diff = R.diff_versions(old_ver, new_ver)

    output = {"new_session": new_sess, "new_settlement": new_sett,
              "old_version": old_ver, "new_version": new_ver, "diff": diff}
    if args.output:
        _save_json(output, args.output)
        print(f"[revise] 新版本与差额已写出：{args.output}")
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    if diff.get("reconfirm_required"):
        print("[revise] ⚠️ 需重新确认：" + "、".join(diff.get("reconfirm_reasons", [])), file=sys.stderr)
    return 0


def cmd_compare(args):
    plans = _load_json(args.plans)
    if not isinstance(plans, list) or len(plans) < 2 or len(plans) > 8:
        raise ValueError("比较需提供 2 至 8 个方案")
    valid, failed = [], []
    baseline = None
    labels = set()
    if any(not isinstance(plan, dict) for plan in plans):
        raise ValueError("每个方案必须为对象")
    for index, plan in enumerate(plans):
        label = plan.get("label", f"plan-{index + 1}")
        try:
            if not isinstance(label, str) or label in labels:
                raise ValueError("方案名称必须为不重复的字符串")
            labels.add(label)
            if plan.get("equivalent_confirmed") is not True:
                raise ValueError("组织者尚未确认此方案满足相同用餐需求")
            sess = _default_session(plan["session"])
            S.validate_session(sess)
            context = (sess.get("store"), sess.get("channel"), sorted(m["id"] for m in sess["members"]))
            if baseline is None:
                baseline = context
            if context != baseline:
                raise ValueError("方案门店、渠道或成员不一致，不能直接比较")
            q = plan["quote"] if args.mode == "real" else S.build_demo_quote(sess)
            S.validate_quote(sess, q, real=args.mode == "real")
            result = S.compute_settlement(sess, q)
            S.require_valid_settlement(result)
            valid.append({"label": label, "payable_cents": result["official_payable_cents"],
                          "quote_source": result["quote_source"], "quote_id": result["quote_id"],
                          "quote_time": result["quote_time"], "input_hash": result["input_hash"]})
        except (ValueError, KeyError, TypeError) as exc:
            failed.append({"label": label, "reason": str(exc)})
    if len(valid) < 2:
        print(json.dumps({"blocked": True, "reason": "有效报价不足两种，不能形成比较结论",
                          "valid_count": len(valid), "failed": failed}, ensure_ascii=False), file=sys.stderr)
        return 1
    ranked = sorted(valid, key=lambda p: p["payable_cents"])
    minimum = ranked[0]["payable_cents"]
    output = {"mode": args.mode, "ranking": ranked,
              "lowest_in_compared_plans": [p["label"] for p in ranked if p["payable_cents"] == minimum],
              "failed": failed,
              "note": "仅比较组织者确认需求一致且核价成功的方案，不保证全局最低；补贴不作为官方优惠。"}
    if args.output:
        _save_json(output, args.output)
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def cmd_render(args):
    result = _load_json(args.result)
    S.require_valid_settlement(result)
    fmt = args.format
    if fmt == "json":
        out = RR.render_json(result, desensitize=not args.no_desensitize)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)
        else:
            print(out)
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
        "references/settlement-rules.md",
        "references/mcp-workflow.md",
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
        "items": [{"item_id": "i1", "qty": 1, "unit_price_cents": 3000}],
        "public_fees": {"delivery_cents": 0, "packaging_cents": 0, "service_cents": 0},
        "subsidy": {"type": "whole_order", "whole_order_cents": 900, "eligible": "ALL"},
    }
    q = S.build_demo_quote(sample)
    res = S.compute_settlement(sample, q)
    ok_cons = res["conservation"]["sum_personal_plus_company_equals_official"]
    check("样例金额守恒", ok_cons, f"官方应付={q['official_payable_cents']}分")
    ok_subsidy = all(m["personal_pays_cents"] >= 0 for m in res["members"])
    check("补贴不造成个人自付为负", ok_subsidy)
    check("均分样例公司补贴 900 分", res["totals"]["subsidy_total_cents"] == 900)

    # 4) MCP 工具发现（不自动检查，避免把“未查询”标为通过）
    print("[INFO] MCP 工具发现：未自动检查（运行时由宿主执行 tools/list）；真实核价与改单记录见 docs/integration-log.md")

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
    r.add_argument("--quote-old", help="真实模式：改单前官方核价 JSON（文件或内联）")
    r.add_argument("--quote-new", help="真实模式：改单后官方核价 JSON（文件或内联）")
    r.add_argument("--prepare", action="store_true", help="只应用变更，输出新需求和待核价输入")
    r.add_argument("--output")
    r.set_defaults(func=cmd_revise)

    cmp = sub.add_parser("compare", help="比较已确认需求一致的方案报价")
    cmp.add_argument("--plans", required=True, help="方案列表 JSON；每项含 label/session/quote/equivalent_confirmed")
    cmp.add_argument("--mode", choices=["demo", "real"], default="real")
    cmp.add_argument("--output")
    cmp.set_defaults(func=cmd_compare)

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
    try:
        return args.func(args)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f"[{args.cmd}] 操作被阻止：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
