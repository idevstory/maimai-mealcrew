"""revisions.py — 方案版本、状态与改单差额（纯函数，仅标准库）。

设计要点（见 docs/mcd-group-meal-assistant-mvp.md）：
- 每次改单生成新版本，保留上一版需求与账单。
- 重新核验商品可售、券适用、满减门槛与叠加规则，重新官方核价；旧报价仅用于对比。
- 商品/金额/门店/渠道变化后旧确认失效，需重新确认。
- 状态机：DRAFT → READY → QUOTED → SETTLED → CONFIRMED；改单回到 DRAFT/READY。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_STATES = ["DRAFT", "READY", "QUOTED", "SETTLED", "CONFIRMED"]


def next_state(current: str, event: str) -> str:
    """极简状态机推进；改单事件一律回到 DRAFT（需重新确认）。"""
    transitions = {
        "DRAFT": {"confirm_requirements": "READY"},
        "READY": {"quote": "QUOTED"},
        "QUOTED": {"settle": "SETTLED"},
        "SETTLED": {"confirm": "CONFIRMED"},
        "CONFIRMED": {"revise": "DRAFT"},
    }
    return transitions.get(current, {}).get(event, current)


def make_version(label: str, session: dict, quote: dict, settlement: dict,
                 state: str = "SETTLED") -> dict:
    """构造一个不可变的方案版本快照。"""
    return {
        "version": label,
        "created_at": now_iso(),
        "state": state,
        "session_snapshot": session,
        "quote": quote,
        "settlement": settlement,
    }


def _item_key(it: dict) -> str:
    return it.get("item_id") or it.get("product_code") or str(it)


def _item_signature(it: dict) -> dict:
    return {
        "product_code": it.get("product_code"),
        "name": it.get("name"),
        "qty": it.get("qty", 1),
        "unit_price_cents": it.get("unit_price_cents"),
        "item_discount_cents": it.get("item_discount_cents", 0),
        "bearer_type": it.get("bearer_type"),
        "bearer": it.get("bearer"),
        "shared_members": it.get("shared_members"),
    }


def diff_versions(old: dict, new: dict) -> dict:
    """对比两个版本，产出改单差额与需重新确认事项。"""
    old_s = old.get("session_snapshot", {})
    new_s = new.get("session_snapshot", {})
    old_set = old.get("settlement", {})
    new_set = new.get("settlement", {})

    # 官方总额变化
    old_pay = old_set.get("official_payable_cents", 0)
    new_pay = new_set.get("official_payable_cents", 0)

    # 逐成员个人自付变化
    old_members = {m["id"]: m for m in old_set.get("members", [])}
    new_members = {m["id"]: m for m in new_set.get("members", [])}
    per_member = []
    for mid in sorted(set(old_members) | set(new_members)):
        o = old_members.get(mid, {}).get("personal_pays_cents")
        n = new_members.get(mid, {}).get("personal_pays_cents")
        if o != n:
            per_member.append({
                "member_id": mid,
                "old_personal_pays_cents": o,
                "new_personal_pays_cents": n,
                "delta_cents": (n if n is not None else 0) - (o if o is not None else 0),
                "status": "added" if mid not in old_members else ("removed" if mid not in new_members else "changed"),
            })

    # 商品变化
    old_items = {_item_key(it): _item_signature(it) for it in old_s.get("items", [])}
    new_items = {_item_key(it): _item_signature(it) for it in new_s.get("items", [])}
    item_changes = {"added": [], "removed": [], "changed": []}
    for k in new_items:
        if k not in old_items:
            item_changes["added"].append(k)
        elif new_items[k] != old_items[k]:
            item_changes["changed"].append({"item": k, "from": old_items[k], "to": new_items[k]})
    for k in old_items:
        if k not in new_items:
            item_changes["removed"].append(k)

    # 需重新确认的事项
    reconfirm = []
    if old_s.get("store") != new_s.get("store"):
        reconfirm.append("门店变化")
    if old_s.get("channel") != new_s.get("channel"):
        reconfirm.append("渠道变化")
    if old_pay != new_pay:
        reconfirm.append("官方应付金额变化")
    if item_changes["added"] or item_changes["removed"] or item_changes["changed"]:
        reconfirm.append("商品/规格/数量/归属变化")
    if {m.get("id") for m in old_s.get("members", [])} != {m.get("id") for m in new_s.get("members", [])}:
        reconfirm.append("成员增减")
    if (old_s.get("subsidy") or {}) != (new_s.get("subsidy") or {}):
        reconfirm.append("补贴规则变化")
    if old_s.get("public_fee_split") != new_s.get("public_fee_split"):
        reconfirm.append("公共费用分摊方式变化")
    if old_s.get("settlement_mode") != new_s.get("settlement_mode"):
        reconfirm.append("结算模式变化")

    return {
        "official_total_change_cents": new_pay - old_pay,
        "old_official_payable_cents": old_pay,
        "new_official_payable_cents": new_pay,
        "per_member_changes": per_member,
        "item_changes": item_changes,
        "reconfirm_required": bool(reconfirm),
        "reconfirm_reasons": reconfirm,
    }
