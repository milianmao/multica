# AI 增强工具对比

## 4 个 AI 增强工具

| 工具 | 范式 | AI 依赖 | 选择器策略 | 在验证路线中的位置 |
|------|------|---------|-----------|------------------|
| **Midscene.js** | 视觉驱动 + Skills | VLM | 自然语言 + 可选传统 | 动态 UI 断言（验证路线 Step 3-5） |
| **Stagehand** | 人机协同（act/extract） | LLM | 自然语言 API | 契约测试数据提取（验证路线 Step 5） |
| **Vouch** | 零选择器（纯 VLM） | VLM | 零选择器 | 探索性测试（验证路线 Step 3 前） |
| **Slapwright** | AI 代理专用 CLI | 无（CDP） | peek/tap/type 命令 | 独立工具链（不直接接入验证路线） |

## Midscene.js

**何时用**：动态 UI 元素（Modal / Toast / 动画）的断言，传统 selector 失效场景。

```javascript
import Midscene from '@midscene/web';

const midscene = new Midscene.Agent(page);

// 自然语言断言
await midscene.aiAssert('页面显示"提交成功"通知');

// 自然语言动作
await midscene.aiAction('在验证码弹窗中输入 123456');

// 自然语言查询
const planId = await midscene.aiQuery('当前页面的 Plan ID 是什么？');
```

**环境**：VLM API（推荐 Qwen-VL-Max，中文友好）+ Chrome。

**集成位置**：验证路线 Step 3-5 中，**对动态 UI 部分**用 aiAssert 兜底。

## Stagehand

**何时用**：从页面提取结构化数据（订单列表、用户信息），契约测试场景。

```javascript
import { Stagehand } from "@browserbasehq/stagehand";
import { z } from 'zod';

const stagehand = new Stagehand({ env: "LOCAL" });
await stagehand.init();
const page = stagehand.page();

// 自然语言操作
await page.act("Click the login button");

// 结构化数据提取 + Zod schema
const userInfo = await page.extract({
  instruction: "Extract the user's name and email",
  schema: z.object({
    name: z.string(),
    email: z.string(),
  }),
});
```

**环境**：LLM API（推荐 GPT-4o / Claude-3.5）+ Chrome。

**集成位置**：验证路线 Step 5（契约测试），用 `extract()` + Zod schema 验证 API + UI 一致性。

## Vouch

**何时用**：探索性测试（新功能快速验证 UI 流程）。

```python
# Python SDK
test("用户可以登录") do
  vouch.click("登录按钮")
  vouch.fill("邮箱输入框", "user@example.com")
  vouch.assert("看到欢迎消息")
end
```

**环境**：VLM API + Chrome。

**集成位置**：验证路线 Step 3 之前（探索性验证），不进入正式 E2E 套件。

## Slapwright

**何时用**：AI 代理需要轻量级浏览器交互（CLI 友好）。

```bash
slapwright peek --url http://localhost:8765 --describe "登录页面"
slapwright tap --url http://localhost:8765 --selector "登录按钮"
slapwright type --url http://localhost:8765 --selector "邮箱输入框" --text "user@example.com"
```

**环境**：Chrome（CDP 协议）。

**集成位置**：独立工具链，不直接接入 Playwright 验证路线。适合 Claude Code / MCP 场景。

## AI 工具选用决策

| 项目特征 | 主推 | 备选 |
|---------|------|------|
| 中文 + 1000 行 Playwright | Midscene.js | Stagehand |
| 英文 + < 100 行测试 | Vouch | Midscene.js |
| AI 代理集成（Claude Code / MCP） | Slapwright | Midscene.js |
| 零 AI 预算 | 不用 AI | — |
| 视觉回归对比 | Midscene.js | Vouch |
| 探索性测试 | Vouch / Slapwright | — |

**核心原则**：**永远保留传统 selector 作为兜底**，AI 是增强不是替代。

## 混合模式（推荐）

```javascript
// ✅ 推荐：90% selector + 10% AI
test('完整流程', async ({ page }) => {
  // 稳定路径（90%）：传统 selector（快、准、便宜）
  await page.getByLabel('邮箱').fill('test@example.com');
  await page.getByRole('button', { name: '提交' }).click();
  
  // 动态 UI（10%）：Midscene.js aiAssert 兜底
  await midscene.aiWaitFor('看到成功通知');
});
```

完整示例：`examples/midscene-integration/8-link-flow-with-ai.spec.js`

## 成本与准确率

| 工具 | 单操作成本 | 单操作延迟 | 选择器准确率 |
|------|:---------:|:---------:|:-----------:|
| 传统 Playwright | $0 | <100ms | 100%（硬编码） |
| Stagehand | $0.005-0.02 | 1-3s | 90-95% |
| Midscene.js | $0.01-0.05 | 2-5s | 85-95% |
| Vouch | $0.02-0.08 | 3-8s | 80-90% |
| Slapwright | $0（CDP） | <100ms | N/A（命令式） |

**混合模式成本**：1000 次操作 ≈ $0.5-5（仅 AI 部分计费），准确率 95-99%。

## 反模式

| 反模式 | 后果 |
|--------|------|
| 全用 AI 替代 selector | 成本高、慢、脆弱 |
| Vouch 用于严格 CI | 视觉模型 80-90% 准确率不够 |
| Slapwright 用于完整 E2E | 功能不够 |
| Stagehand 用于视觉回归 | 不擅长视觉 diff |
| AI 工具用于纯后端 | 无 UI 可操作 |

## 决策树

```
Q1: 项目语言？
  ├─ 中文 → Midscene.js（主推）
  └─ 英文 → Q2

Q2: 测试规模？
  ├─ <100 行（小型）→ Vouch
  ├─ 100-500 行（中型）→ Midscene.js
  └─ >500 行（大型）→ Midscene.js + Stagehand 混合

Q3: AI 代理集成？
  ├─ 是（Claude Code / MCP）→ Slapwright
  └─ 否 → Q4

Q4: 测试场景？
  ├─ 探索性 → Vouch
  ├─ 复杂交互（Modal/动画）→ Stagehand
  ├─ 视觉回归 → Midscene.js
  └─ 现有测试改造 → Midscene.js（增量）
```

详细决策矩阵：`references/ai-decision-tree.md`

## 环境要求

| 工具 | Node.js | Chrome | AI API |
|------|:------:|:------:|:------:|
| Midscene.js | ≥18 | ✅ | VLM |
| Stagehand | ≥18 | ✅ | LLM |
| Vouch | ≥16 | ✅ | VLM |
| Slapwright | ≥16 | ✅ | 无 |

完整环境配置：`references/environment-setup.md`