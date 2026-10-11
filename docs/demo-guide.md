# 演示指南 · demo-guide.md

本指南说明如何在**无麦当劳 MCP Token** 的情况下，用合成数据跑通 `麦麦团餐助手` 的完整流程，并区分演示与真实模式。

## 1. 为什么需要演示模式

真实模式依赖官方 `calculate-price` 核价，缺少有效连接时本 Skill **停止核价并给配置指引**，不会伪造实时价格。演示模式用于：开发自测、教学展示、评审离线验证，所有输出明确标注"演示"。

## 2. 运行端到端演示

```bash
python skill/maimai-mealcrew/scripts/mealcrew.py demo --output ./demo_out
```

输出：

- `demo_result.json`：完整结算结果（逐成员餐费、优惠归属、补贴、个人自付、守恒校验）
- `demo_report.html`：单文件 HTML 账单（打印友好）
- 终端打印 Markdown 账单摘要

演示场景（`examples/demo_session.json`，6 人）：含个人餐品、共享餐、配送费、订单级满减（满 200 减 20）、每人 25 元公司补贴。

## 3. 单步演示

```bash
# 归一化需求
python skill/maimai-mealcrew/scripts/mealcrew.py normalize --input examples/demo_session.json --output /tmp/session.json

# 结算 + 渲染 HTML
python skill/maimai-mealcrew/scripts/mealcrew.py settle --session /tmp/session.json --mode demo --render html --output /tmp/bill.html

# 改单：小王退出，重新核价与差额
python skill/maimai-mealcrew/scripts/mealcrew.py revise \
  --session /tmp/session.json \
  --changes '{"remove_members":["m2"]}' --output /tmp/rev.json
```

## 4. 演示 vs 真实 对照

| 维度 | 演示（demo） | 真实（real） |
|---|---|---|
| 核价来源 | 本地数字直接构造（`build_demo_quote`） | 官方 `calculate-price` |
| 商品/优惠 | 合成数据 | 实时菜单与可用券 |
| 输出标注 | 明确"演示" | 来自官方字段 |
| 写操作 | 不执行 | 领券/下单需明确确认 |

## 5. 金额守恒自检

无论演示还是真实，结算结果都保证：

```
所有成员分配合计 == 官方最终应付
公司承担合计 + 个人自付合计 == 官方最终应付
```

运行 `doctor` 或 `python -m unittest discover -s tests` 可验证规则正确性与边界（补贴上限、不可补贴人员、比例尾差等）。
