# 麦麦团餐助手 · Maimai MealCrew

> Group Meal Planning & Split Bills for McDonald's orders.
> 一群人吃麦麦，帮你**整理需求、算清优惠、分好账，改了餐也能重新算清楚**。

`麦麦团餐助手` 是麦当劳程序员节创意开发大赛（[M-China/mcd-developer-innovation-challenge](https://github.com/M-China/mcd-developer-innovation-challenge)）的参赛 Skill 项目。它以可安装的 Agent Skill 形式交付：通过对话收集多人点餐需求，调用麦当劳官方 MCP 核价，再用本地确定性脚本完成分账、补贴与改单重算，最终输出 Markdown / JSON / 单文件 HTML 账单。

> ⚠️ 本项目为参赛者独立开发的比赛作品，**非麦当劳官方产品**。餐品、价格、优惠与供应状态一律以麦当劳官方渠道实时结果为准。

---

## 1. 解决什么问题（产品价值）

多人一起点麦当劳时，组织者常卡在三件事上：**谁点了什么、优惠怎么算、钱怎么分**。本 Skill 用四个价值回应：

| 价值 | 用户得到什么 | 交付能力 |
|---|---|---|
| **核算方便** | 每个人点什么、承担多少，一张账单看清 | 按人餐单 / 共享餐 / 公共费用三类归属，账单金额守恒、分毫不差 |
| **优惠方便** | 比较单点、套餐和适用优惠，知道实际省在哪里 | 多方案官方核价对比，逐项列明优惠依据与未采用原因 |
| **补贴方便** | 公司补多少、个人补多少，规则清楚 | 支持每人补贴与全单补贴，明确资格与费用覆盖范围，补贴不转负 |
| **改餐方便** | 增减人、换餐后，优惠和每个人的金额一起更新 | 方案版本留痕，自动重新核价、输出逐人差额并提示重新确认 |

产品价值表述：**需求好整理，优惠算得清，补贴好分配，改餐能重算。**

## 2. 目标用户

- 同事拼午餐 / 加班餐的组织者
- 朋友聚餐、活动订餐的发起人
- 需要核算公司餐补（本地结算规则）的团队

产品中的"团餐"泛指**多人用餐**；普通到店、外带、外送属于不同业务渠道，企业团餐需以实时接口与权限为准。

## 3. 两种运行模式

| 模式 | 说明 | 触发条件 |
|---|---|---|
| **真实模式（real）** | 调用已配置的麦当劳 MCP 做菜单查询与 `calculate-price` 核价，金额以官方为准 | 宿主已配置 `mcd-mcp` 且 Token 有效 |
| **离线演示（demo）** | 使用合成数据跑通完整流程，**所有结果明确标注为演示**，不输出伪实时价格 | 无 Token / 无连接时默认可用，或显式 `--mode demo` |

真实模式缺少有效连接时，**停止核价并给出配置指引**，不会静默切到演示冒充实时。

## 4. 环境要求

- **Python 3.9+**（仅使用标准库，无需 `pip install` 任何依赖即可运行核心流程）
- 宿主（推荐 WorkBuddy）已配置麦当劳 MCP（`mcd-mcp`）；配置见 `mcp-config.example.json`
- 真实核价需要有效的 `MCD_MCP_TOKEN` 环境变量（经宿主或环境变量提供，**不写入公开文件**）

## 5. 安装方法

### 方式 A：在 WorkBuddy 中安装（推荐，官方合作宿主）

1. 左侧边栏【专家·技能·连接器】→【连接器】→【自定义连接器】→【配置 MCP】。
2. 填入 `mcp-config.example.json` 的内容，将 `Bearer ${MCD_MCP_TOKEN}` 改为 `Bearer <你的真实 Token>`，保存并启用 `mcd-mcp`。
3. 将本仓库的 `skill/maimai-mealcrew/` 目录作为独立 Skill 安装（或导入 `dist/` 下生成的 ZIP）。
4. 新开一个会话，用自然语言触发（见第 6 节示例）。

> 安装只更新 Skill 自身文件，**不覆盖用户已有 MCP 配置**；凭证通过宿主设置或环境变量提供。

### 方式 B：其他 Agent 宿主

任意支持 Agent Skill（含 `SKILL.md` + 资源相对路径加载）的宿主均可。安装后先运行自检：

```bash
python skill/maimai-mealcrew/scripts/mealcrew.py doctor
```

## 6. 使用示例（自然语言入口）

1. **整理需求**："帮我们 6 个人点麦当劳，人均预算 35 元，公司补贴 25 元，超出的自己付。"
2. **匹配与核价**："这是大家的点餐消息，整理一下，看看套餐和券怎么用更划算。"
3. **结算与改单**："小王不来了，重新算一下满减和每个人的钱。" / "小李换大份，跟上一版比差多少钱？"

命令行（核心流程，输入/输出均为 JSON）：

```bash
# 离线演示：6 人合成场景端到端
python skill/maimai-mealcrew/scripts/mealcrew.py demo

# 结构化需求 -> 归一化会话
python skill/maimai-mealcrew/scripts/mealcrew.py normalize --input examples/demo_session.json

# 结算（demo 核价）+ 渲染账单
python skill/maimai-mealcrew/scripts/mealcrew.py settle --session session.json --mode demo --render html

# 改单：生成新版本并对比差额
python skill/maimai-mealcrew/scripts/mealcrew.py revise --session session.json --changes changes.json

# 自检
python skill/maimai-mealcrew/scripts/mealcrew.py doctor
```

子命令：`normalize` / `candidates` / `settle` / `revise` / `render` / `demo` / `doctor`。

## 7. 能力边界（诚实声明）

- 金额只来自**官方核价**或**确定性本地计算**；角色文案不得覆盖用户约束与脚本结果。
- 公司补贴是**本地结算规则**，不是官方优惠券，不改变麦当劳应收金额。
- 优惠券仅使用组织者**已授权账号**的可用券；领取、付费权益与下单需各自明确确认。
- 真实写操作（领券 `auto-bind-coupons`、创建/查询/取消订单）在用户逐步明确确认后才执行，默认只做**只读核价 + 本地分账**，不在核价流程中静默下单。
- 官方仅返回总额而无完整折扣归因时，标明"内部约定分摊"，由组织者确认总额分配规则，不把本地推算当作官方逐项账单。
- 未验证的平台不声明已适配；未验证的接口（如 `query-promotions` 企业团餐满减）不模拟规则。

## 8. 结果截图 / 演示素材

- 真实模式联调截图与脱敏记录见 `docs/integration-log.md`（联调后补全）。
- 离线演示账单样例由 `demo` 子命令生成，可导出 HTML/PNG。
- 约 60–90 秒流程视频在发布阶段录制并放入仓库（资料需准确区分模拟与实时）。

## 9. 参赛材料清单

| 文件 | 说明 |
|---|---|
| `README.md` | 本文件（介绍 / 安装 / 示例 / 目标用户 / 边界） |
| `CONTEST_DECLARATION.md` | 官方参赛声明（内容不可改动） |
| `MCP_INTEGRATION.md` | 实际 MCP Server / Tools / 调用流程 / 业务价值 |
| `mcp-config.example.json` | 官方连接示例，仅环境变量占位符 |
| `skill/maimai-mealcrew/` | 可安装 Skill 主体（SKILL.md + 脚本 + 参考 + 模板 + 样例） |
| `tests/` | 业务规则与异常验证（金额守恒、补贴上限、尾差等） |
| `docs/` | 安装、演示、脱敏联调记录 |
| `workbuddy.md` | 申请 WorkBuddy 专项奖励时替换为真实开发上下文 |
| `dist/` | 可导入 ZIP（发布时用 `build_dist.py` 生成） |

## 10. 许可证与声明

- 代码采用 MIT 许可证（详见仓库 LICENSE）。
- 本项目为大赛参赛作品，非麦当劳官方产品；麦当劳相关商标与餐品信息归麦当劳所有，使用以官方实时数据为准。
- 详见 `CONTEST_DECLARATION.md`。
