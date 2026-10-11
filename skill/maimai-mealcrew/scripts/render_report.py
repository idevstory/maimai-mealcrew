"""render_report.py — 账单渲染：Markdown / JSON / 单文件 HTML（默认脱敏）。

设计约束：
- 默认隐藏地址、电话、账号标识等敏感字段（脱敏）。
- HTML 为账单呈现层，不承担真实支付或保存 Token。
- HTML 生成失败时，仍保留 JSON 与 Markdown（由调用方处理降级）。
"""

from __future__ import annotations

import json
import os
from typing import Dict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMPLATE = os.path.join(SCRIPT_DIR, "..", "templates", "report.html")

# 脱敏字段（键名匹配即遮蔽其值）
DENY_KEYS = {"phone", "mobile", "tel", "address", "address_detail", "account_id", "token", "contact", "id_card"}


def to_yuan(cents: int) -> str:
    return f"¥{cents / 100:.2f}"


def redact(obj, denylist=DENY_KEYS):
    """递归遮蔽敏感字段。"""
    if isinstance(obj, dict):
        return {
            k: ("***" if k in denylist else redact(v, denylist))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v, denylist) for v in obj]
    return obj


def render_json(result: dict, desensitize: bool = True) -> str:
    data = redact(result) if desensitize else result
    return json.dumps(data, ensure_ascii=False, indent=2)


def render_markdown(result: dict, desensitize: bool = True) -> str:
    data = redact(result) if desensitize else result
    lines = []
    lines.append("# 麦麦团餐账单 · MealCrew Bill")
    lines.append("")
    lines.append(f"- 会话：{data.get('session_id') or '—'}")
    lines.append(f"- 结算模式：{data.get('mode')}")
    official = data.get("official_payable_cents", 0)
    lines.append(f"- 官方应付：**{to_yuan(official)}**")
    lines.append(f"- 守恒校验：{'通过' if data.get('conservation', {}).get('sum_personal_plus_company_equals_official') else '未通过 ⚠️'}")
    lines.append("")
    lines.append("## 按人账单")
    lines.append("")
    lines.append("| 成员 | 餐费 | 公共费用 | 优惠归属 | 补贴前 | 公司承担 | 个人自付 |")
    lines.append("|---|---|---|---|---|---|---|")
    for m in data.get("members", []):
        lines.append(
            f"| {m.get('name')} | {to_yuan(m.get('meal_cost_cents', 0))} "
            f"| {to_yuan(m.get('fee_share_cents', 0))} "
            f"| {to_yuan(m.get('discount_alloc_cents', 0))} "
            f"| {to_yuan(m.get('pre_subsidy_cents', 0))} "
            f"| {to_yuan(m.get('company_bears_cents', 0))} "
            f"| **{to_yuan(m.get('personal_pays_cents', 0))}** |"
        )
    totals = data.get("totals", {})
    lines.append("")
    lines.append("## 优惠与费用汇总")
    lines.append("")
    lines.append(f"- 商品净价合计：{to_yuan(totals.get('items_net_cents', 0))}")
    lines.append(f"- 订单级优惠：{to_yuan(totals.get('order_discount_cents', 0))}")
    lines.append(f"- 公共费用：{to_yuan(totals.get('public_fees_cents', 0))}")
    lines.append(f"- 公司补贴合计：{to_yuan(totals.get('subsidy_total_cents', 0))}")
    lines.append("")
    lines.append("> 本项目为大赛参赛作品，非麦当劳官方产品；金额以官方实时核价为准。")
    return "\n".join(lines)


def _bill_table_html(members: list) -> str:
    rows = []
    for m in members:
        rows.append(
            "<tr>"
            f"<td>{m.get('name')}</td>"
            f"<td>{to_yuan(m.get('meal_cost_cents', 0))}</td>"
            f"<td>{to_yuan(m.get('fee_share_cents', 0))}</td>"
            f"<td>{to_yuan(m.get('discount_alloc_cents', 0))}</td>"
            f"<td>{to_yuan(m.get('pre_subsidy_cents', 0))}</td>"
            f"<td>{to_yuan(m.get('company_bears_cents', 0))}</td>"
            f"<td class='pay'>{to_yuan(m.get('personal_pays_cents', 0))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>成员</th><th>餐费</th><th>公共费用</th><th>优惠归属</th>"
        "<th>补贴前</th><th>公司承担</th><th>个人自付</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_html(result: dict, template_path: str = DEFAULT_TEMPLATE, desensitize: bool = True) -> str:
    data = redact(result) if desensitize else result
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            tpl = f.read()
    except FileNotFoundError:
        # 模板缺失时降级为极简 HTML，仍保留数据
        return (
            "<!doctype html><meta charset='utf-8'><h1>MealCrew Bill</h1><pre>"
            + render_json(data, desensitize=False)
            + "</pre>"
        )
    official = data.get("official_payable_cents", 0)
    recon_ok = data.get("conservation", {}).get("sum_personal_plus_company_equals_official")
    summary = (
        f"官方应付 {to_yuan(official)} ｜ 守恒校验 "
        f"{'通过' if recon_ok else '未通过'}"
    )
    html = (
        tpl.replace("{{TITLE}}", "麦麦团餐账单 · MealCrew Bill")
        .replace("{{GENERATED_AT}}", data.get("session_id") or "—")
        .replace("{{MODE}}", str(data.get("mode")))
        .replace("{{OFFICIAL_PAYABLE}}", to_yuan(official))
        .replace("{{BILL_TABLE}}", _bill_table_html(data.get("members", [])))
        .replace("{{SUMMARY}}", summary)
        .replace("{{DISCLAIMER}}", "大赛参赛作品，非麦当劳官方产品；金额以官方实时核价为准。")
    )
    return html
