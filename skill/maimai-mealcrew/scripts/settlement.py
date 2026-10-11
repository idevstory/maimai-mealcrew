"""settlement.py — 团餐分摊、补贴与金额守恒（纯函数，仅标准库）。

设计约束（见 docs/mcd-group-meal-assistant-mvp.md）：
- 内部金额一律使用整数分（cents），避免浮点误差。
- 保证金额守恒：
    所有成员分配合计 == 官方最终应付金额
    公司承担合计 + 个人自付合计 == 官方最终应付金额
- 补贴不得造成任何个人自付为负；未用额度保留，不自动转移。
- 比例分摊采用最大余数法（Hamilton），分配确定性、可复现。

本模块不依赖任何外部服务，可在无 Token 的离线环境下被 tests/ 完整验证。
"""

from __future__ import annotations

from typing import Dict, List, Optional
from fractions import Fraction
import hashlib
import json
from datetime import datetime


def cents_from_yuan(yuan: float) -> int:
    """元 -> 整数分（四舍五入）。"""
    return int(round(float(yuan) * 100))


def yuan_from_cents(cents: int) -> float:
    """整数分 -> 元（浮点，仅用于展示）。"""
    return cents / 100.0


def split_equally(total_cents: int, n: int) -> List[int]:
    """把 total_cents 均分到 n 份，返回长度 n 的列表，合计 == total_cents。

    余数从前到后各 +1（确定性）。n<=0 返回空列表。
    """
    if n <= 0:
        return []
    if total_cents <= 0:
        return [0] * n
    base = total_cents // n
    rem = total_cents % n
    return [base + (1 if i < rem else 0) for i in range(n)]


def allocate_proportional(total_cents: int, weights: List[float]) -> List[int]:
    """按权重把 total_cents 分到各参与方（最大余数法）。

    返回与 weights 等长的整数列表，合计 == total_cents。
    - total_cents == 0：返回全 0。
    - 权重和 <= 0：退化为均分，避免 total>0 时全 0 造成守恒失败。
    """
    n = len(weights)
    if n == 0:
        return []
    if total_cents == 0:
        return [0] * n
    weights = [Fraction(str(w)) for w in weights]
    if any(w < 0 for w in weights):
        raise ValueError("权重不能为负")
    wsum = sum(weights)
    if wsum <= 0:
        return split_equally(total_cents, n)
    # 向下取整分配
    allocated = [(w * total_cents) // wsum for w in weights]
    remainder = total_cents - sum(allocated)
    # 余数分给小数部分最大的参与方（确定性）
    order = sorted(range(n), key=lambda i: ((weights[i] * total_cents) % wsum), reverse=True)
    for k in range(int(remainder)):
        allocated[order[k]] += 1
    return allocated


def nonnegative_int(value, field):
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} 必须为非负整数")
    return value


def validate_session(session: dict) -> None:
    if not isinstance(session, dict):
        raise ValueError("会话必须为对象")
    members = session.get("members", [])
    if not isinstance(members, list) or not members or any(not isinstance(m, dict) for m in members):
        raise ValueError("members 必须为非空对象列表")
    ids = [m.get("id") for m in members]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("成员 id 必须为非空且不重复的字符串")
    mode = session.get("settlement_mode", "itemized")
    if mode not in ("itemized", "equal_split"):
        raise ValueError("settlement_mode 非法")
    if session.get("public_fee_split", "equal") not in ("equal", "by_meal_cost"):
        raise ValueError("public_fee_split 非法")
    items = session.get("items", [])
    if not isinstance(items, list) or any(not isinstance(it, dict) for it in items):
        raise ValueError("items 必须为对象列表")
    item_ids = [it.get("item_id") for it in items]
    if any(not isinstance(i, str) or not i for i in item_ids) or len(item_ids) != len(set(item_ids)):
        raise ValueError("商品 item_id 必须为非空且不重复的字符串")
    for it in items:
        iid = it["item_id"]
        qty = nonnegative_int(it.get("qty", 1), f"{iid}.qty")
        if qty == 0:
            raise ValueError("商品数量必须为正整数")
        unit = nonnegative_int(it.get("unit_price_cents", 0), f"{iid}.unit_price_cents")
        disc = nonnegative_int(it.get("item_discount_cents", 0), f"{iid}.item_discount_cents")
        if disc > unit * qty:
            raise ValueError("商品级优惠超过该商品金额")
        bt = it.get("bearer_type", "person")
        if bt == "person":
            if mode != "equal_split" and it.get("bearer") not in ids:
                raise ValueError("商品个人归属不在成员列表中")
        elif bt == "shared":
            sm = it.get("shared_members", [])
            if not isinstance(sm, list) or not sm or any(m not in ids for m in sm) or len(sm) != len(set(sm)):
                raise ValueError("共享成员必须为非空、不重复的有效成员列表")
        else:
            raise ValueError("未知 bearer_type")
    scope = session.get("order_discount_scope", "ALL")
    if scope is None or scope == "ALL":
        scoped = item_ids
    elif isinstance(scope, list) and scope and all(i in item_ids for i in scope) and len(scope) == len(set(scope)):
        scoped = scope
    else:
        raise ValueError("优惠范围为空、重复或含未知商品，需重新核价")
    od = nonnegative_int(session.get("order_discount_cents", 0), "order_discount_cents")
    base = sum(it.get("unit_price_cents", 0) * it.get("qty", 1) - it.get("item_discount_cents", 0)
               for it in items if it["item_id"] in scoped)
    if od > base:
        raise ValueError("订单级优惠超过适用商品净额，需重新核价")
    subsidy = session.get("subsidy", {"type": "none"})
    if not isinstance(subsidy, dict) or subsidy.get("type", "none") not in ("none", "per_person", "whole_order"):
        raise ValueError("subsidy 非法")
    for key in ("per_person_cents", "whole_order_cents"):
        nonnegative_int(subsidy.get(key, 0), f"subsidy.{key}")
    if type(subsidy.get("covers_public_fees", True)) is not bool:
        raise ValueError("covers_public_fees 必须为布尔值")
    elig = subsidy.get("eligible", "ALL")
    if elig not in (None, "ALL"):
        if not isinstance(elig, list) or any(e not in ids for e in elig) or len(elig) != len(set(elig)):
            raise ValueError("补贴资格必须为不重复的有效成员列表")
    fees = session.get("public_fees", {})
    if not isinstance(fees, dict) or set(fees) - {"delivery_cents", "packaging_cents", "service_cents"}:
        raise ValueError("public_fees 含未知费用或不是对象")
    for key, value in fees.items():
        nonnegative_int(value, f"public_fees.{key}")


def quote_input(session):
    """仅包含核价输入，内部补贴和归属变化可以复用同一官方报价。"""
    return {
        "store": session.get("store"), "channel": session.get("channel", "takeaway"),
        "items": [{k: it.get(k, default) for k, default in (
            ("item_id", None), ("product_code", None), ("qty", 1),
            ("unit_price_cents", 0), ("item_discount_cents", 0),
            ("options", {}), ("coupon_ids", []))} for it in session.get("items", [])],
        "public_fees": {k: session.get("public_fees", {}).get(k, 0)
                        for k in ("delivery_cents", "packaging_cents", "service_cents")},
        "order_discount_cents": session.get("order_discount_cents", 0),
        "order_discount_scope": session.get("order_discount_scope", "ALL"),
        "coupon_ids": session.get("coupon_ids", []),
    }


def input_hash(session):
    data = json.dumps(quote_input(session), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def quote_request(session):
    validate_session(session)
    return {"source": "mcp-request", "session_id": session.get("session_id", "session-1"),
            "input_hash": input_hash(session), "quote_preview": None,
            "pricing_input": quote_input(session),
            "note": "本地归一化输入，不是官方 API 参数；宿主必须按 tools/list 的 Schema 转换并调用核价。"}


def validate_quote(session, quote, real=False):
    if not isinstance(quote, dict) or "official_payable_cents" not in quote:
        raise ValueError("缺少核价结果或最终应付金额")
    nonnegative_int(quote["official_payable_cents"], "official_payable_cents")
    if real:
        if quote.get("source") != "mcp":
            raise ValueError("真实模式只接受 source=mcp 的报价")
        if quote.get("session_id") != session.get("session_id", "session-1"):
            raise ValueError("报价与当前会话不匹配")
        if quote.get("input_hash") != input_hash(session):
            raise ValueError("报价与门店、渠道、商品、费用或优惠输入不匹配，需重新核价")
        if not isinstance(quote.get("quote_id"), str) or not quote["quote_id"]:
            raise ValueError("缺少 quote_id")
        try:
            timestamp = datetime.fromisoformat(quote["quote_time"].replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError("核价时间必须含时区")
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError("quote_time 必须为带时区的 ISO 8601 时间") from exc
        expected = {
            "subtotal_cents": sum(it.get("unit_price_cents", 0) * it.get("qty", 1) for it in session.get("items", [])),
            "item_discount_cents": sum(it.get("item_discount_cents", 0) for it in session.get("items", [])),
            "order_discount_cents": session.get("order_discount_cents", 0),
            "public_fees_cents": sum(session.get("public_fees", {}).values()),
        }
        for key, amount in expected.items():
            if key in quote and nonnegative_int(quote[key], key) != amount:
                raise ValueError(f"官方 {key} 与归一化明细不一致")


def require_valid_settlement(result):
    if not result["conservation"]["sum_personal_plus_company_equals_official"] or not result["reconciliation"]["ok"]:
        raise ValueError("金额守恒或明细对账失败，已阻止正式账单")
    if any(m["personal_pays_cents"] < 0 for m in result["members"]):
        raise ValueError("个人自付不能为负")


def _resolve_scope(item_ids: List[str], scope) -> List[str]:
    """解析订单级优惠适用范围（仅用于合法范围过滤，不负责“扩大”）。

    返回有效商品 id 列表；显式（非 ALL）范围若过滤后为空，调用方会据此抛错。
    """
    if scope is None or scope == "ALL":
        return list(item_ids)
    if isinstance(scope, (list, tuple)):
        return [i for i in scope if i in item_ids]
    return list(item_ids)


def _distribute_to_bearers(
    amount: int,
    bearer_type: str,
    bearer: Optional[str],
    shared_members: List[str],
    member_ids: List[str],
) -> Dict[str, int]:
    """把一笔金额按归属分摊到成员：person -> {bearer: amount}；shared -> 均分到 shared_members。"""
    if amount == 0:
        return {}
    if bearer_type == "person":
        if bearer not in member_ids:
            raise ValueError(f"person 商品归属未知成员: {bearer}")
        return {bearer: amount}
    if bearer_type == "shared":
        targets = [m for m in shared_members if m in member_ids]
        if not targets:
            raise ValueError(f"shared 商品未指定有效共享成员: {shared_members}")
        shares = split_equally(amount, len(targets))
        return {m: s for m, s in zip(targets, shares)}
    raise ValueError(f"未知 bearer_type: {bearer_type}")


def build_demo_quote(session: dict) -> dict:
    """离线演示：以会话中的商品/费用数字直接构造一份 demo 报价（source='demo'）。

    真实模式不应调用本函数；真实金额来自官方 calculate-price。
    """
    validate_session(session)
    items = session.get("items", [])
    gross = sum(int(it.get("unit_price_cents", 0)) * int(it.get("qty", 1)) for it in items)
    item_discount = sum(int(it.get("item_discount_cents", 0)) for it in items)
    fees = session.get("public_fees", {})
    fee_total = (
        int(fees.get("delivery_cents", 0))
        + int(fees.get("packaging_cents", 0))
        + int(fees.get("service_cents", 0))
    )
    order_discount = int(session.get("order_discount_cents", 0))
    payable = gross - item_discount - order_discount + fee_total
    payable = max(0, payable)
    return {
        "quote_id": "demo-quote",
        "session_id": session.get("session_id", "session-1"),
        "input_hash": input_hash(session),
        "source": "demo",
        "official_payable_cents": payable,
        "subtotal_cents": gross,
        "item_discount_cents": item_discount,
        "order_discount_cents": order_discount,
        "public_fees_cents": fee_total,
        "quote_time": "demo",
        "field_notes": ["演示数据：金额由本地数字直接构造，非官方实时核价"],
    }


def compute_settlement(session: dict, quote: dict, opts: Optional[dict] = None) -> dict:
    """核心结算：分摊优惠、公共费用与补贴，返回逐成员账单与守恒校验。

    入参 session 结构见 references/data-contract.md；quote 至少含 official_payable_cents。
    返回结构可在 tests/ 中验证金额守恒、补贴上限、四舍五入与不可补贴人员等规则。
    """
    opts = opts or {}
    validate_session(session)
    validate_quote(session, quote)
    members = session.get("members", [])
    member_ids = [m["id"] for m in members]
    items = session.get("items", [])
    mode = session.get("settlement_mode", "itemized")
    fees = session.get("public_fees", {})
    fee_total = (
        int(fees.get("delivery_cents", 0))
        + int(fees.get("packaging_cents", 0))
        + int(fees.get("service_cents", 0))
    )
    public_fee_split = session.get("public_fee_split", "equal")
    order_discount = int(session.get("order_discount_cents", 0))
    subsidy = session.get("subsidy", {"type": "none"})

    official_payable = int(quote.get("official_payable_cents", 0))

    # 1) 每件商品净成本（商品级优惠已扣）与订单级优惠归属
    item_ids = [it.get("item_id") or f"i{idx}" for idx, it in enumerate(items)]
    line_gross = []
    line_net_before_order = []
    item_discount_list = []
    for it in items:
        qty = int(it.get("qty", 1))
        unit = int(it.get("unit_price_cents", 0))
        idisc = int(it.get("item_discount_cents", 0))
        gross = unit * qty
        net = max(0, gross - idisc)
        line_gross.append(gross)
        line_net_before_order.append(net)
        item_discount_list.append(idisc)

    scoped = _resolve_scope(item_ids, session.get("order_discount_scope", "ALL"))
    scoped_explicit = not (session.get("order_discount_scope", "ALL") in (None, "ALL"))
    # 显式范围为空或净额为零：非法/失效，阻止结算并要求重新核价，不得退回全单
    if scoped_explicit and not scoped:
        raise ValueError("订单级优惠适用范围内无有效商品，需重新核价（不可退回全单）")
    scoped_weights = [
        line_net_before_order[i] if item_ids[i] in scoped else 0 for i in range(len(items))
    ]
    if scoped_explicit and order_discount > 0 and sum(scoped_weights) <= 0:
        raise ValueError("订单级优惠适用范围内净额为 0，需重新核价（不可退回全单）")
    order_alloc = allocate_proportional(order_discount, scoped_weights) if order_discount > 0 else [0] * len(items)

    # 2) 商品净成本与优惠归属分摊到成员
    member_meal = {m: 0 for m in member_ids}
    member_discount = {m: 0 for m in member_ids}
    for idx, it in enumerate(items):
        if mode == "equal_split":
            continue  # 均分模式忽略逐商品归属
        net_i = max(0, line_net_before_order[idx] - order_alloc[idx])
        disc_i = item_discount_list[idx] + order_alloc[idx]
        bearer_type = it.get("bearer_type", "person")
        bearer = it.get("bearer")
        shared = it.get("shared_members", [])
        net_dist = _distribute_to_bearers(net_i, bearer_type, bearer, shared, member_ids)
        disc_dist = _distribute_to_bearers(disc_i, bearer_type, bearer, shared, member_ids)
        for m, v in net_dist.items():
            member_meal[m] += v
        for m, v in disc_dist.items():
            member_discount[m] += v

    # 3) 公共费用分摊
    if mode == "equal_split":
        meal_total = sum(line_net_before_order) - order_discount
        meal_shares = split_equally(meal_total, len(member_ids))
        # 先均分总额，再按餐费份额拆出费用，避免两次独立尾差使 AA 不均。
        pre_shares = split_equally(meal_total + fee_total, len(member_ids))
        member_meal = dict(zip(member_ids, meal_shares))
        member_pre = dict(zip(member_ids, pre_shares))
        member_fee = {m: member_pre[m] - member_meal[m] for m in member_ids}
        member_discount = dict(zip(member_ids, split_equally(sum(item_discount_list) + order_discount, len(member_ids))))
    else:
        if public_fee_split == "by_meal_cost":
            weights = [member_meal[m] for m in member_ids]
        else:  # equal
            weights = [1] * len(member_ids)
        fee_dist = allocate_proportional(fee_total, weights) if fee_total > 0 else [0] * len(member_ids)
        member_fee = {m: v for m, v in zip(member_ids, fee_dist)}
        member_pre = {m: member_meal[m] + member_fee[m] for m in member_ids}

    # 4) 补贴（本地结算规则，不改变官方应收）
    eligible = subsidy.get("eligible", "ALL")
    if eligible == "ALL" or eligible is None:
        eligible_set = set(member_ids)
    else:
        eligible_set = set(eligible)

    # covers_public_fees=False 时，补贴只覆盖餐费（不含公共费用）；
    effective_covers = subsidy.get("covers_public_fees", True)

    company_bears = {m: 0 for m in member_ids}
    personal_pays = {}
    subsidy_type = subsidy.get("type", "none")

    if subsidy_type == "per_person":
        per = int(subsidy.get("per_person_cents", 0))
        for m in member_ids:
            base = member_meal[m] if not effective_covers else member_pre[m]
            applied = min(per, base) if m in eligible_set else 0
            company_bears[m] = applied
            personal_pays[m] = member_pre[m] - applied
    elif subsidy_type == "whole_order":
        total_sub = int(subsidy.get("whole_order_cents", 0))
        weights = [
            (member_meal[m] if not effective_covers else member_pre[m]) if m in eligible_set else 0
            for m in member_ids
        ]
        wsum = sum(weights)
        cap = min(total_sub, wsum)  # 公司补贴不超过可补贴基数之和（超出额度保留不使用）
        alloc = allocate_proportional(cap, weights) if cap > 0 else [0] * len(member_ids)
        for i, m in enumerate(member_ids):
            company_bears[m] = alloc[i]
            personal_pays[m] = member_pre[m] - alloc[i]
    else:  # none
        for m in member_ids:
            company_bears[m] = 0
            personal_pays[m] = member_pre[m]

    # 5) 守恒校验
    # 逐商品口径：成员餐费合计 + 公共费用 == 官方应付（仅 itemized 模式可据此对账）
    items_net_plus_fees = sum(member_meal[m] for m in member_ids) + fee_total
    subsidy_total = sum(company_bears.values())
    sum_personal = sum(personal_pays.values())
    conservation_ok = (sum_personal + subsidy_total) == official_payable

    member_out = []
    for m in members:
        mid = m["id"]
        member_out.append({
            "id": mid,
            "name": m.get("name", mid),
            "meal_cost_cents": member_meal[mid],
            "fee_share_cents": member_fee[mid],
            "discount_alloc_cents": member_discount[mid],
            "pre_subsidy_cents": member_pre[mid],
            "company_bears_cents": company_bears[mid],
            "personal_pays_cents": personal_pays[mid],
        })

    return {
        "session_id": session.get("session_id"),
        "mode": mode,
        "official_payable_cents": official_payable,
        "quote_source": quote.get("source", "unknown"),
        "quote_id": quote.get("quote_id"),
        "input_hash": quote.get("input_hash"),
        "quote_time": quote.get("quote_time") or quote.get("created_at"),
        "quote_notes": quote.get("field_notes") or quote.get("notes") or [],
        "reconciliation": {
            "computed_items_net_plus_fees_cents": items_net_plus_fees,
            "official_payable_cents": official_payable,
            "delta_cents": items_net_plus_fees - official_payable,
            "ok": items_net_plus_fees == official_payable,
        },
        "totals": {
            "items_net_cents": sum(line_net_before_order),
            "order_discount_cents": order_discount,
            "public_fees_cents": fee_total,
            "subsidy_total_cents": subsidy_total,
        },
        "members": member_out,
        "conservation": {
            "sum_personal_plus_company_equals_official": conservation_ok,
            "sum_personal_cents": sum_personal,
            "sum_company_cents": subsidy_total,
            "official_cents": official_payable,
        },
        "rounding_note": "比例/均分采用最大余数法，确保分配合计等于官方应付，无隐藏尾差。",
    }
