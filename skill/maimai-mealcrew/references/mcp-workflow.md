# 宿主 MCP 核价与改单

本地 Python 不直接联网；由已配置官方麦当劳 MCP 的宿主发现工具、查询菜单和券、调用核价。以下是本地协议，不是官方 API Schema。必须按运行时 `tools/list` 得到的真实 Schema 转换参数，不能把本地 JSON 原样当作官方请求。

## 初次核价

1. 整理需求，确认个人/共享餐及内部费用规则，生成 `session.json`。
2. 由宿主查询实际门店、渠道、商品编码、规格及适用券。只有官方核验后的数据可用于真实模式。
3. `python scripts/mealcrew.py candidates --session session.json --mode real` 输出 `pricing_input`、`session_id` 与 `input_hash`。
4. 宿主按真实 Schema 调用官方核价。将实际返回的金额单位转换为整数分，并同步商品价格、优惠及公共费用到 session。若最终明细变化，重新运行 candidates 取得最终明细的指纹；这些明细必须能与本次官方请求和响应逐项关联，不可补造缺失优惠。
5. 生成 `quote.json`：`source=mcp`、官方报价/调用记录标识 `quote_id`、带时区 ISO 8601 的实际调用时间 `quote_time`、最终 `session_id`、最终 `input_hash` 和 `official_payable_cents`。可同时保存官方小计、优惠、费用；存在时本地会逐项核对。
6. `python scripts/mealcrew.py settle --session session.json --mode real --quote quote.json --render html --output bill.html`。失败时先解决诊断，不进入确认或下单。

`input_hash` 是本地会话关联校验，不是官方签名，不能证明调用发生过。`source=mcp` 也是宿主归一化元数据。真实使用证据须来自实际调用与脱敏联调记录。报价代表指定时间的一次结果，不是锁价；下单前须由宿主检查是否需要再次核价。

## 两阶段改单

先保留原 session、原 quote 和变更 `changes.json`：

```bash
python scripts/mealcrew.py revise --session session.json --changes changes.json --mode real --prepare --output prepared.json
```

`prepared.json` 含 `new_session` 和 `quote_request`，不包含新账单。将 `new_session` 单独保存为文件供宿主核价；若官方返回导致价格或优惠字段变化，同步更新 `changes.json` 的商品 patch 和相关费用/优惠字段，再重新 prepare。

取得匹配新输入的报价后完成：

```bash
python scripts/mealcrew.py revise --session session.json --changes changes.json --mode real --quote-old quote-old.json --quote-new quote-new.json --output revised.json
```

两份报价必须分别匹配原、新方案，且两个结算均通过。结果保存旧、新版本快照、逐人差额与重确认原因。新会话回到 DRAFT，清除旧授权；结算完成不自动代表用户确认。

仅改变内部补贴和归属时，同一真实报价可复用，因为这些字段不进入官方核价指纹。优惠适用范围等字段变化必须取得新输入对应的报价。

## 同需求方案比较

由宿主从真实菜单与可用券构造替代套餐/券方案，组织者确认预算、份量、偏好和人数等需求一致后，分别核价。不要削减需求声称便宜。

`plans.json` 为 2 至 8 项数组，每项包含 `label`、`session`、`quote`、`equivalent_confirmed: true`。true 只能在组织者已确认需求一致后填写；Python 不会判断不同餐品营养或份量是否等价。

```bash
python scripts/mealcrew.py compare --plans plans.json --mode real --output comparison.json
```

只比较门店、渠道、成员一致且核价、对账通过的方案，展示成功方案排名和失败原因；少于两份有效报价时阻断。最低结论只覆盖已比较方案，不承诺全局最低。没有适用券时直接说明，不虚构优惠。

## 联调记录

安装到目标宿主后，应实际完成原餐单核价、一次改单和至少两种可行方案比较。记录宿主与版本、时间、工具、核价来源、报价摘要及对账结果。只保存脱敏信息，不写入 Token、详细地址、手机号或会员账号。无连接时明确未验证，不能用合成测试替代真实证据。
