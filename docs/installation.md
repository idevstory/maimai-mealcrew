# 安装与运行 · installation.md

## 环境要求

- Python 3.9+（核心流程仅用标准库，无需 `pip install`）
- 宿主：推荐 WorkBuddy（官方合作平台）；其他支持 Agent Skill 的宿主亦可
- 真实核价需要有效的 `MCD_MCP_TOKEN`（经宿主或环境变量提供）

## 步骤一：配置麦当劳 MCP（真实模式）

参考官方指南 [mcd-mcp-server](https://github.com/M-China/mcd-mcp-server) 申请 Token，然后在宿主配置 `mcd-mcp`：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

> 也可直接复制仓库根 `mcp-config.example.json`，把 `Bearer ${MCD_MCP_TOKEN}` 改为真实 Token。
> **真实 Token 只通过宿主设置或环境变量提供，绝不写入公开文件。**

## 步骤二：安装 Skill

- **WorkBuddy**：左侧【专家·技能·连接器】→ Skill 安装，选择本仓库 `skill/maimai-mealcrew/` 目录（或导入 `dist/` 下的 ZIP）。
- **其他宿主**：将 `skill/maimai-mealcrew/` 作为独立 Skill 目录安装，确保宿主按相对路径加载 `SKILL.md` 与 `scripts/`。

## 步骤三：自检

```bash
cd skill/maimai-mealcrew
python scripts/mealcrew.py doctor
```

`doctor` 会检查目录完整性、Python 运行时、样例计算（金额守恒 + 补贴不转负）与可见工具说明。本地检查通过后，真实使用仍需按 [MCP 工作流](../skill/maimai-mealcrew/references/mcp-workflow.md) 验证连接、核价和改单。

## 步骤四：新会话触发

新开一个会话，用自然语言触发，例如：

> 帮我们 6 个人点麦当劳，人均预算 35 元，公司补贴 25 元，超出的自己付。

## 离线演示（无需 Token）

```bash
python scripts/mealcrew.py demo --output ./demo_out
```

生成 `demo_result.json` 与 `demo_report.html`，所有结果标注"演示"，不代表官方实时价格。

## 安装边界

- 安装只更新 Skill 自身文件，**不覆盖用户已有 MCP 配置**。
- 本地状态保存在明确指定的任务目录（`sessions/`），不打包进公开 Skill。
- 自检范围：目录完整性、运行时、样例计算、可见工具；**自检不领取券、不创建订单**。
