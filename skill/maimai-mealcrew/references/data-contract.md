# 数据契约 · data-contract.md

本地状态与脚本 IO 的字段约定。内部金额一律使用**整数分（cents）**，与官方字段契约转换时以官方返回为准。

## MealSession（需求与规则）

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | str | 稳定会话标识 |
| `store` | str? | 门店（外送时为配送门店） |
| `channel` | `"takeaway" \| "delivery"` | 取餐渠道 |
| `members` | list[{id, name}] | 稳定成员 ID 与昵称 |
| `settlement_mode` | `"itemized" \| "equal_split"` | itemized=按归属分摊；equal_split=全单均分 |
| `public_fee_split` | `"equal" \| "by_meal_cost"` | 公共费用分摊方式 |
| `items` | list[DemandItem] | 餐品明细 |
| `public_fees` | {delivery_cents, packaging_cents, service_cents} | 公共费用（整数分） |
| `order_discount_cents` | int | 订单级优惠（满减/满折）总额 |
| `order_discount_scope` | `"ALL" \| [item_id]` | 优惠适用范围 |
| `subsidy` | SubsidyRule | 公司补贴规则 |

### DemandItem

| 字段 | 类型 | 说明 |
|---|---|---|
| `item_id` | str | 稳定商品行 ID |
| `product_code` | str? | 真实商品编码（来自 query-meals） |
| `name` | str? | 展示名 |
| `qty` | int | 数量 |
| `unit_price_cents` | int | 官方单价（已含商品级活动价/套餐价） |
| `item_discount_cents` | int | 该商品专属优惠（如商品券），减少该商品净成本 |
| `bearer_type` | `"person" \| "shared"` | 个人承担 / 共享 |
| `bearer` | str? | person 时的成员 ID |
| `shared_members` | [str] | shared 时的共享成员 ID 列表 |

### SubsidyRule（本地结算规则，非官方优惠券）

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | `"none" \| "per_person" \| "whole_order"` | 补贴方式 |
| `per_person_cents` | int | per_person 时每人补贴上限 |
| `whole_order_cents` | int | whole_order 时总补贴 |
| `eligible` | `"ALL" \| [member_id]` | 适用人员 |
| `covers_public_fees` | bool | 是否含公共费用 |

## Quote（核价结果）

| 字段 | 类型 | 说明 |
|---|---|---|
| `quote_id` | str | 报价标识 |
| `source` | `"demo" \| "mcp"` | 来源 |
| `official_payable_cents` | int | **官方最终应付**（金额唯一权威） |
| `subtotal_cents` | int? | 商品小计 |
| `item_discount_cents` | int? | 商品级优惠合计 |
| `order_discount_cents` | int? | 订单级优惠 |
| `public_fees_cents` | int? | 公共费用合计 |
| `quote_time` | str | 核价时间（报价仅对应某次核价，非锁价） |
| `field_notes` | [str] | 字段缺失 / 演示标注等说明 |

## Settlement（结算结果）

`compute_settlement` 返回：每个成员的 `meal_cost_cents`、`fee_share_cents`、`discount_alloc_cents`、`pre_subsidy_cents`、`company_bears_cents`、`personal_pays_cents`；以及 `reconciliation`（逐项对账）与 `conservation`（个人+公司==官方）校验。

## Revision（版本与差额）

`diff_versions(old, new)` 返回：官方总额变化、逐成员个人自付变化、商品增删改、需重新确认事项（`reconfirm_required` / `reconfirm_reasons`）。

## Confirmation（确认）

确认的方案版本、商品与报价标识、费用规则、授权动作；**不包含 Token**。商品/金额/门店/渠道变化后旧确认失效。
