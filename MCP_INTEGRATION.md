# MCP 集成说明 · MCP_INTEGRATION.md

本文件说明 `麦麦团餐助手` 实际使用的麦当劳 MCP Server、Tool、调用流程与业务价值，并标注**已验证**与**待验证**项，避免对未核验接口做承诺。

> 说明：本项目以 **只读核价 + 本地分账** 为核心交付。以下工具清单依据官方 `mcd-mcp-server` 文档与公开接入指南整理；凡标注"待 tools/list 核验"者，均不模拟其行为，仅在运行时动态发现实际 Schema。

## 1. MCP Server

| 项 | 值 |
|---|---|
| Server 名称 | `mcd-mcp` |
| 传输方式 | Streamable HTTP |
| 地址 | `https://mcp.mcp.cn` → 实际为 `https://mcp.mcd.cn` |
| 鉴权 | `Authorization: Bearer ${MCD_MCP_TOKEN}` |
| 配置样例 | `mcp-config.example.json`（仅占位符，无真实凭证） |

凭证由宿主管理，Skill 不读取、不存储 Token；本仓库任何文件均不含真实 Token。

## 2. 实际使用的 Tools 与调用流程

```text
用户对话 / 粘贴消息
  → Skill 提取与澄清 → 已确认的结构化需求
  → 宿主 MCP 发现工具（tools/list，确认实际 Schema 与权限）
  → query-nearby-stores        门店（外送时 delivery-query-addresses / delivery-query-stores）
  → query-meals / query-meal-detail   菜单、套餐组成、替换选项
  → query-store-coupons / available-coupons / query-my-coupons / campaign-calendar  优惠
  → calculate-price            候选方案官方核价（金额以官方为准）
  → 本地脚本分摊优惠 / 公共费用 / 补贴
  → Markdown + JSON + HTML 账单
  → 改单形成新版本 → 重查必要数据 → 重新核价与结算
```

### 工具职责表（核心链路）

| 阶段 | Tool | 用途与规则 | 状态 |
|---|---|---|---|
| 能力发现 | `tools/list`（宿主） | 确认工具存在、字段、渠道、权限；记录已验证契约 | **已规划，待首次联调核验** |
| 门店 | `query-nearby-stores` | 只查询选定渠道与必要范围；不替用户默认选择歧义地址 | 待联调 |
| 商品匹配 | `query-meals`、`query-meal-detail` | 匹配规格、套餐组成和修改选项；不用模型记忆代替实时菜单 | 待联调 |
| 优惠查询 | `query-store-coupons`、`available-coupons`、`query-my-coupons`、`campaign-calendar` | 当前门店、账号、渠道可用性必须核验；活动日历只提供线索 | 待联调 |
| 候选核价 | `calculate-price` | 以当前商品、数量、规格、优惠核价；金额与单位按字段契约解析 | 待联调 |

### 需明确确认后才执行的写操作

| 阶段 | Tool | 说明 | 状态 |
|---|---|---|---|
| 可选领券 | `auto-bind-coupons` | 经明确确认才执行；成功后重新查询可用券与核价 | 待核验 |
| 企业团餐 | `query-meal-assistance`（仅企业团餐）；`query-promotions` | 官方工具表未列出 `query-promotions`，需 `tools/list` 核验，未发现则跳过并说明 | **待核验，不模拟** |
| 后续订单 | `create-order`、`query-order`、`cancel-order` | 独立设计与验收，经用户明确确认后才执行 | 待核验 |

## 3. 渠道与单位契约

- **渠道（channel）**：`takeaway`（到店/外带）、`delivery`（外送）。外送涉及 `delivery-query-addresses` 与 `delivery-query-stores`，地址/电话输出脱敏。
- **金额单位**：内部一律使用**整数分（cents）**；与官方字段契约转换（元/分）时以官方返回字段类型为准，缺失单位时阻止最终账单并提示核验。
- **报价时效**：`Quote` 仅对应某次核价输入与时间，不是锁价承诺；商品/优惠输入变化必须重新核价。
- **金额守恒**：所有成员分配合计 = 官方最终应付；公司承担合计 + 个人自付合计 = 官方最终应付。

## 4. 业务价值映射

| 产品价值 | 依赖的 MCP 能力 | 本地实现部分 |
|---|---|---|
| 核算方便 | `calculate-price` 计算商品金额、配送费、优惠与应付总价；`query-meals`/`query-meal-detail` 提供菜单与套餐组成 | 按人餐单、共享归属、费用分配、整数分与对账 |
| 比单点优惠 | `query-store-coupons`/`available-coupons`/`query-my-coupons`/`campaign-calendar` | 比较同需求下候选方案，计入配送/包装等实际费用，列出核价比较范围 |
| 补贴方便 | 官方未提供企业餐补/AA/多付款人接口 | 用户配置补贴规则、资格、分摊；导出内部结算单（不宣称通过 MCP 自动发补贴/扣款） |
| 改餐方便 | `query-meal-detail`、`calculate-price` 可重新核价 | 未下单方案增删改、重匹配优惠、重分账与确认版本 |

## 5. 已验证 / 未验证

- **已验证（文档层面）**：官方 README 声明服务覆盖团餐；`calculate-price` 明确计算商品金额、配送费、优惠金额与应付总价；`v1.0.5` 支持更换套餐内组合、`v1.0.9` 新增 `cancel-order`。
- **待真实联调核验**：具体 `tools/list` Schema、某门店菜单与可用券、券叠加规则、金额单位、满减门槛。需至少在一个真实门店完成原餐单与改单后的核价（见 `docs/integration-log.md`）。
- **不承诺**：团餐总比单点便宜；任意订单可零成本改餐；企业团餐满减可用（未核验接口不模拟）。

## 6. 降级与异常处理

| 情况 | 处理 |
|---|---|
| 无 Token / 工具不可用 / 401 | 保留需求，给配置或权限指引，不输出伪实时价格 |
| 429 / 超时 / 部分候选失败 | 有限重试；展示成功比较范围，失败候选不进入最优结论 |
| 单位未知 / 总额不可信 / 分摊不守恒 | 阻止最终账单，提示核验字段契约 |
| 无券 / 券失效 / 不能叠加 | 返回真实无优惠/可用单券方案，不擅自凑单 |
| HTML 生成失败 | 保留 JSON 与 Markdown，明确部分输出未完成 |
