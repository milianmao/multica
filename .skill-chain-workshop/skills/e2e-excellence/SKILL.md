---
name: e2e-excellence
version: 2.3
description: 浏览器端 E2E 验证方法论。提供确定性使用门禁（gate）、验证方法、验证路线。聚焦"如何准确验证产品行为"，不涉及 bug 修复流程（请用 fix-bug-workflow）。v2.3 新增 Gate 11 行为门禁（点击后必须看到 DOM 状态变化）+ Gate 12 设计稿契约对比（防止 AI 声明完成但 UI 没改）。v2.2 新增 Gate waiver 机制：上游可声明 pre-existing Gate 失败项豁免阻塞（CI 模式禁用）。v2.1 支持 fix-bug/deep-planning/team-rigorous 渐进式披露调用。
when_to_use:
  - "新项目设计 E2E 测试架构"
  - "现有项目验证质量不达标（假绿、断言弱、selector 脆）"
  - "CI 上 E2E flake 率高"
  - "需要 3 层验证策略（冒烟/回归/契约）"
  - "需要断言质量门禁（避免假绿）"
  - "需要 selector 鲁棒性检查（语义查询）"
  - "被上游 skill（fix-bug/deep-planning/team-rigorous）渐进式披露调用"
  - "Gate 失败但属 pre-existing（非本次引入），需豁免阻塞"
  - "AI 声明完成但需交叉验证 UI 实际渲染（v2.3 新增场景）"
skip_when:
  - "无浏览器 UI（纯 CLI/纯后端）"
  - "纯单元测试场景（用 test-pyramid-builder）"
  - "bug 修复流程（请用 fix-bug-workflow）"
  - "CI 环境（waiver 禁用，必须真修）"
---

# E2E Excellence v2.0 — 浏览器验证方法论

## 1. 本 skill 做什么 / 不做什么

### ✅ 做什么
- 定义 E2E 验证的**确定性门禁**（gate）：什么算"验证通过"
- 提供**验证方法**：配置模板、断言质量、selector 策略、3 层验证策略
- 提供**验证路线**：从写测试到跑测试到失败定位的完整步骤
- 提供**自检工具**：本地/CI 跑 `e2e_doctor.py` 验证配置与断言质量
- 在合适位置引用 **AI 增强工具**（Midscene.js / Stagehand / Vouch / Slapwright）

### ❌ 不做什么
- 不涉及 bug 修复流程（那是 `fix-bug-workflow`）
- 不替代单元测试 / 组件测试（那是 `test-pyramid-builder`）
- 不评估测试框架选型（Playwright/Cypress 已确定用 Playwright）
- 不写"优化前后对比"叙事（这是抽象方法论，不是案例报告）

---

## 2. 确定性使用门禁（Gate）

每个 E2E 项目必须满足以下 10 项门禁，缺一不可。

### Gate 1: webServer 自动启停

**规则**：playwright.config.js 必须包含 `webServer` 字段，Playwright 自动启停测试环境。

**模板**：`references/playwright-config-template.js`

**验证命令**：`python3 scripts/e2e_doctor.py playwright.config.js` → 检查通过

### Gate 2: trace 保留失败现场

**规则**：`use.trace` 必须设为 `'on-first-retry'` 或 `'on'`，失败时保留 trace.zip。

**为什么不是 `retain-on-failure`**：trace 是开发者最关键的定位工具，必须保留完整 DOM 快照 + 网络 + 控制台。

**验证命令**：`e2e_doctor.py` 检查 `trace` 字段。

### Gate 3: video 保留时序证据

**规则**：`use.video` 必须设为 `'retain-on-failure'`，失败时录制 video.webm。

**验证命令**：`e2e_doctor.py` 检查 `video` 字段。

### Gate 4: reuseExistingServer 适配 CI/本地

**规则**：`webServer.reuseExistingServer` 必须为 `!isCI`（本地复用，CI 强制新拉）。

**验证命令**：`e2e_doctor.py` 检查 `reuseExistingServer` 字段。

### Gate 5: retries 配置（保证 trace 触发）

**规则**：配置 `retries: process.env.CI ? 2 : 0`（CI 重试 2 次，本地不重试）。

**为什么需要**：Gate 2 的 `trace: 'on-first-retry'` 只有在 retry 时才生成 trace。

**验证命令**：`e2e_doctor.py` 检查 `retries` 字段。

### Gate 6: 验证策略分层

**规则**：测试目录必须分层：
```
tests/e2e/
├── smoke/          # 冒烟：主流程入口可达
├── regression/     # 回归：每个历史 bug 一条
└── contract/       # 契约：API 响应结构稳定
```

**命名规范**：`<name>.smoke.spec.js` / `<name>.regression.spec.js` / `<name>.contract.spec.js`

**验证命令**：`e2e_doctor.py` 检查目录结构 + 命名规范。

### Gate 7: selector 鲁棒性

**规则**：语义查询（`getByRole` / `getByLabel` / `getByText`）占比 ≥ 50%。

**反模式**：
```javascript
// ❌ 硬编码 selector（UI 改了测试崩）
page.locator('.btn-primary').click();
page.locator('#login-form > div:nth-child(3) > input').fill('email');
```

**推荐**：
```javascript
// ✅ 语义查询（UI 改了仍能找到）
page.getByRole('button', { name: '登录' }).click();
page.getByLabel('邮箱').fill('email@example.com');
```

**验证命令**：`e2e_doctor.py` 扫描 .spec.js，统计语义查询 / 硬编码 selector 比例。

### Gate 8: 断言质量

**规则**：弱断言（`toBeTruthy` / `toBeDefined` / `waitForTimeout`）占比 < 10%。

**反模式**：
```javascript
// ❌ 弱断言（假绿高发）
expect(value).toBeTruthy();
await page.waitForTimeout(3000);
expect(items.length).toBeGreaterThan(0);
```

**推荐**：
```javascript
// ✅ 强断言（精确验证）
expect(response.status()).toBe(201);
await page.waitForResponse('**/api/v1/projects');
expect(items).toHaveLength(3);
```

**完整 checklist**：`references/assertion-quality.md`

**验证命令**：`e2e_doctor.py` 扫描 .spec.js，统计弱断言占比。

### Gate 9: expect.timeout ≥ 8000ms

**规则**：`expect.timeout` 设为 ≥ 8000ms（SPA 默认 5s 太短）。

**验证命令**：`e2e_doctor.py` 检查 `expect.timeout`。

### Gate 10: SKIP_AUTOSTART 兼容开关

**规则**：保留 `V5_E2E_SKIP_AUTOSTART=1` 环境变量兼容（不破坏已有 `run-local.sh` 工作流）。

**验证命令**：`e2e_doctor.py` 检查环境变量支持。

### Gate 11: 行为门禁（v2.3 新增，防"AI 声明完成但 UI 没改"）

**规则**：每个声称"已完成"的 UI 变更，E2E 必须断言**点击后 DOM 状态发生了预期变化**，而不仅仅是"按钮存在"或"端点返 200"。

**为什么需要**：
- 2026-07-24 真实案例：v6.8.0 §3.6 要求把"拖入文件上传"改成"关联知识库引导流"
- AI 声明"FE8 任务完成 ✅"，代码里确实加了 `KnowledgeProjectSection` 子组件
- 但实际：① v5-workspace 的 ProjectWorkspacePage 没改（dropzone 还在）② 主应用 ProjectDetailPage 有重复 import 整个路由挂掉 ③ prop-types 缺失 4 个组件 import 失败
- **根因**：deep-planning 的 grep 只查"代码存在"，不查"UI 渲染"；E2E 只断言"端点 200"，不断言"点击后状态变化"

**行为门禁模板**（每个 UI 变更点必填）：

```typescript
// ❌ 弱断言（Gate 11 拒绝）
test('知识库入口存在', async ({ page }) => {
  await page.goto('#projects');
  await expect(page.getByText('知识库')).toBeVisible();  // 只查存在
});

// ✅ 行为门禁（Gate 11 通过）
test('点击关联知识库按钮后弹出引导流弹窗', async ({ page }) => {
  await page.goto('#projects');
  await login(page, 'admin', 'admin123');

  // 1. 进入项目详情
  await page.getByRole('button', { name: '进入项目 alpha' }).click();
  await expect(page.getByRole('heading', { name: 'alpha' })).toBeVisible();

  // 2. 找到"关联知识库"按钮（注意：不是旧的"拖入文件"dropzone）
  const guideBtn = page.getByTestId('open-knowledge-guide');
  await expect(guideBtn).toBeVisible();

  // 3. 关键：点击后必须看到弹窗（DOM 状态变化）
  await guideBtn.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('heading', { name: '关联知识库' })).toBeVisible();

  // 4. 验证旧 UI 已删除（dropzone 不应存在）
  await expect(page.locator('.knowledge-dropzone')).toHaveCount(0);

  // 5. Tab 切换行为
  await page.getByTestId('tab-new').click();
  await expect(page.getByPlaceholder('例如：团队 PRD 知识库')).toBeVisible();
});
```

**行为门禁 5 项 checklist**（每个 UI 变更点都要过）：

| # | 检查项 | 断言模式 |
|---|--------|---------|
| 1 | 入口元素存在 | `await expect(button).toBeVisible()` |
| 2 | 点击后状态变化 | `await expect(dialog).toBeVisible()` 或 `await expect(list).toHaveCount(N)` |
| 3 | 旧 UI 已删除 | `await expect(oldSelector).toHaveCount(0)` |
| 4 | 关键文案匹配设计稿 | `await expect(page.getByText('关联知识库')).toBeVisible()` |
| 5 | 错误态不静默 | console error 监听 + `expect(errorCount).toBe(0)` |

**验证命令**：`e2e_doctor.py` 检查 spec 文件中 `.toHaveCount(0)` / `getByRole('dialog')` / `getByTestId` 的出现频率（行为门禁信号）。

### Gate 12: 设计稿契约对比（v2.3 新增，防"实现跑偏设计"）

**规则**：当设计文档（如 `docs/v6.8.0/前端设计.md`）明确定义了 UI 结构时，E2E 必须提取设计稿关键节点 -> 写成断言 -> 与实际 DOM 对比。

**为什么需要**：
- 2026-07-24 案例：v6.8.0 §3.6 设计要求"Tab1 选已有库 + Tab2 新建库"
- AI 实现了 inline Surface 卡片（不是弹窗），违反设计
- 单元测试全绿，因为测试只验证"表单能提交"，不验证"形态是 modal 不是 inline"

**设计稿契约提取流程**：

```bash
# 1. 从设计文档提取关键 UI 节点（grep §x.y 标题下的 Mermaid/列表）
grep -A 20 "§3.6\|### 3.6" docs/v6.8.0/前端设计.md | \
  grep -E "按钮|弹窗|Tab|表单|列表|引导流"

# 2. 每个关键节点写成一条断言
# 设计稿说"点击'关联知识库'按钮 -> 弹出引导流" -> 断言 dialog 出现
# 设计稿说"Tab1 选已有库 / Tab2 新建库" -> 断言两个 tab 可切换
# 设计稿说"新建后自动绑定 + 跳转详情页" -> 断言 URL 变化 + binding 记录存在
```

**设计稿契约对比模板**：

```typescript
// 从 docs/v6.8.0/前端设计.md §3.6 提取的契约
const DESIGN_CONTRACT_3_6 = {
  entry: '关联知识库按钮',           // §3.6 入口
  form: '弹窗（modal）而非 inline 卡片', // §3.6 形态
  tabs: ['关联已有库', '新建并绑定'],   // §3.6 Tab 结构
  new_kb_auto_bind: 'primary 绑定',    // §3.6 自动绑定
  new_kb_redirect: '#knowledge/{id}',  // §3.6 跳转
};

test('§3.6 设计稿契约：关联知识库引导流', async ({ page }) => {
  // 每条契约都写成断言
  await page.goto('#projects');
  await login(page);

  // 契约 1：入口按钮存在
  await page.getByRole('button', { name: '进入项目 alpha' }).click();
  await expect(page.getByText(DESIGN_CONTRACT_3_6.entry)).toBeVisible();

  // 契约 2：点击后是弹窗（不是 inline）
  await page.getByTestId('open-knowledge-guide').click();
  await expect(page.getByRole('dialog')).toBeVisible();  // modal 不是 Surface

  // 契约 3：两个 Tab
  for (const tabName of DESIGN_CONTRACT_3_6.tabs) {
    await expect(page.getByRole('button', { name: tabName })).toBeVisible();
  }

  // 契约 4 + 5：新建后自动绑定 + 跳转（走真实 API）
  await page.getByTestId('tab-new').click();
  await page.getByPlaceholder('例如：团队 PRD 知识库').fill('E2E 契约测试库');
  await page.getByTestId('create-and-bind-submit').click();
  await expect(page).toHaveURL(/#knowledge\/\d+/);
});
```

**验证命令**：
```bash
# 检查 spec 文件是否引用了设计文档契约
grep -rl "DESIGN_CONTRACT\|§[0-9]" tests/e2e/*.spec.js
# 检查契约覆盖率（每个设计稿 §x.y 是否有对应 spec）
python3 skills/e2e-excellence/scripts/design_contract_coverage.py docs/v6.8.0/前端设计.md tests/e2e/
```

**适用场景**：当 deep-planning 声明"按 §x.y 落地"时，必须有对应 §x.y 的契约对比 E2E。无契约 = 不得声明完成。

---

## 3. 验证方法（How to Verify）

### 3.1 3 层验证策略

详见 `references/validation-strategy.md`

| 层 | 目的 | 数量 | 耗时 | 何时跑 |
|----|------|:----:|:----:|--------|
| **冒烟 (smoke)** | 主流程入口可达 | 5-15 条 | 30s-2min | 每个 PR |
| **回归 (regression)** | 防止已修 bug 复发 | 20-50 条 | 2-10min | 每个 PR |
| **契约 (contract)** | API 响应结构稳定 | 5-10 条 | 1-5min | 部署前 |

### 3.2 断言质量 6 项 checklist

详见 `references/assertion-quality.md`

1. 不写假阳性断言（`toBeTruthy` 等）
2. 不依赖 `waitForTimeout`，用 `waitForResponse` / `waitForSelector`
3. 断言具体内容，不只断言存在
4. 用语义查询，不用硬编码 selector
5. API 断言 schema（Zod），不只断言 status
6. 不写"通过即弃"的测试（用 `test.fixme` 标记已知问题）

### 3.3 selector 鲁棒性 4 个等级

| 等级 | 查询方式 | 鲁棒性 |
|------|---------|:------:|
| ⭐⭐⭐⭐⭐ | `getByRole({ name })` | UI 改版仍能找到 |
| ⭐⭐⭐⭐ | `getByLabel({ name })` | 表单结构改版仍能找到 |
| ⭐⭐⭐ | `getByText({ text })` | 文案改版失效 |
| ⭐⭐ | `locator('.class')` | class 改名失效 |
| ⭐ | `locator('#id')` | id 改名失效 |

**目标**：⭐⭐⭐⭐ 及以上占比 ≥ 50%。

---

## 4. 验证路线（Verification Roadmap）

从零搭建到持续运行的完整路线：

```
Step 1: 初始化配置
  └── cp references/playwright-config-template.js → playwright.config.js
  └── 验证: e2e_doctor.py 通过 10 项 Gate

Step 2: 建立 3 层目录
  └── mkdir -p tests/e2e/{smoke,regression,contract}

Step 3: 写冒烟测试（5-15 条）
  └── 参考 examples/validation-ability-demo/validation-ability.spec.js
  └── 覆盖主流程入口可达性

Step 4: 写回归测试（每个历史 bug 一条）
  └── 文件命名: <bug-id>-<short-desc>.regression.spec.js
  └── 内容: 重现 bug 步骤 + 断言修复后行为

Step 5: 写契约测试（每个核心 API 一条）
  └── 断言响应字段 + 类型 + 必填项
  └── 用 Zod schema 验证

Step 6: 本地跑通
  └── npx playwright test
  └── 所有 Gate 通过 → 进入 CI

Step 7: CI 集成
  └── CI=true npx playwright test（reuseExistingServer=false）
  └── 上传 test-results 到 artifacts

Step 8: 失败定位（如果 CI 红）
  └── 下载 trace.zip
  └── npx playwright show-trace trace.zip
  └── 用 Trace Viewer 逐帧回放
```

---

## 5. AI 增强工具（按需启用）

**核心原则**：**永远保留传统 selector 作为兜底**，AI 是增强不是替代。

详见 `references/ai-tools-comparison.md` 和 `references/ai-decision-tree.md`

### 何时使用 AI 工具

| 场景 | 推荐工具 | 在验证路线中的位置 |
|------|---------|-----------------|
| **稳定路径**（标准表单） | 传统 selector（Gate 7） | Step 3-5 |
| **动态 UI**（Modal/Toast/动画） | **Midscene.js**（中文友好） | Step 3-5 的"动态 UI 断言" |
| **结构化数据提取** | **Stagehand** extract API | Step 5 契约测试 |
| **探索性测试**（新功能快速验证） | **Vouch** | Step 3 前 |
| **AI 代理自动化**（MCP 集成） | **Slapwright** | 独立工具链 |

### 集成位置

```javascript
// Step 3 冒烟测试中：稳定路径用 selector
await page.getByRole('button', { name: '提交' }).click();

// Step 3 冒烟测试中：动态 UI 用 Midscene.js 兜底
await midscene.aiAssert('页面显示"提交成功"通知');
```

### 混合模式（推荐）

```javascript
// ✅ 推荐：90% selector + 10% AI
test('完整流程', async ({ page }) => {
  // 稳定 UI: selector
  await page.getByLabel('邮箱').fill('test@example.com');
  await page.getByRole('button', { name: '提交' }).click();
  
  // 动态 UI（Modal/Toast）: AI 兜底
  await midscene.aiWaitFor('看到成功通知');
});
```

完整示例：`examples/midscene-integration/8-link-flow-with-ai.spec.js`

---

## 6. 环境依赖

详见 `references/environment-setup.md`

**最低要求**：
- Node.js ≥ 18
- Chrome / Edge 最新版
- Playwright Chromium

**AI 工具额外要求**：
- Midscene.js / Vouch → VLM API（推荐 Qwen-VL-Max，中文友好）
- Stagehand → LLM API（推荐 GPT-4o / Claude-3.5）
- Slapwright → 无（纯 CDP）

---

## 7. 自检工具（确定性门禁验证）

```bash
# 跑全部 10 项 Gate 检查
python3 skills/e2e-excellence/scripts/e2e_doctor.py playwright.config.js

# 🆕 v2.2 豁免 pre-existing Gate 失败项（CI 禁用）
python3 skills/e2e-excellence/scripts/e2e_doctor.py playwright.config.js \
  --waiver "Gate 8: 历史文件弱断言（pre-existing）"
```

**门禁模式**（v2.2）：输出 PASS / FAIL / WARN / WAIVED，**不打分**（门禁是死的）。

```
[PASS] Gate 1: webServer 自动启停
[WAIVED] Gate 8: 断言质量  🔶 WAIVED (原 FAIL): ... | waiver 理由: 历史文件弱断言
...
判定: PASS_WITH_WARN - 7 PASS | 2 WARN | 0 FAIL | 1 WAIVED
```

**退出码**：FAIL > 0 -> 退出 1（CI 串联用）；WAIVED 不计入 FAIL。

**CI 集成**：
```bash
# CI 模式（waiver 自动禁用，必须真修）
CI=1 python3 skills/e2e-excellence/scripts/e2e_doctor.py playwright.config.js || exit 1
```

---

## 8. 详细文档

| 文档 | 内容 |
|------|------|
| `references/playwright-config-template.js` | 完整配置模板（10 项 Gate 一键启用） |
| `references/validation-strategy.md` | 3 层验证策略详解（smoke/regression/contract） |
| `references/assertion-quality.md` | 6 项断言质量 checklist + 反模式 |
| `references/ai-tools-comparison.md` | 4 个 AI 工具深度对比 |
| `references/ai-decision-tree.md` | 根据项目特征选 AI 工具的决策树 |
| `references/environment-setup.md` | 环境依赖完整清单 + 安装脚本 |
| `references/ci-integration.md` | CI 集成方案（GitHub Actions / GitLab） |

## 9. 实战模板

| 模板 | 用途 |
|------|------|
| `examples/validation-ability-demo/` | 验证能力演示（6 测试覆盖核心 Gate） |
| `examples/midscene-integration/` | Midscene.js 混合模式集成示例 |
| `examples/v5-workspace-2026-07-23/` | Playwright 配置实例 |

---

## 10. 与其他 skill 的边界

| Skill | 关系 | 何时用 |
|-------|------|--------|
| **test-pyramid-builder** | 互补（不同层） | L3 组件测试 + L4 单元测试 |
| **e2e-excellence（本 skill）** | 互补（不同层） | **L0 E2E 浏览器测试** |
| **fix-bug-workflow** | 不重叠 | bug 修复流程（发现/复现/定位/修复） |
| **deep-planning-rigorous-validation** | 上游 | Stage 5 验证阶段 |

**关键边界**：
- 本 skill 只管"如何测、测什么、断言怎么写"
- **不管**"bug 怎么修、修复流程怎么走"
- 互补不重叠，协作不越界

---

## 11. 被调用接口（v2.1 渐进式披露）

本 skill 支持被上游 skill **渐进式披露**调用--上游到达 E2E 验证阶段时，把控制权交给本 skill 跑完验证，再带着结果返回上游继续 downstream。

### 适用上游 skill

| 上游 skill | 调用点 | 调用模式 |
|-----------|--------|---------|
| **fix-bug-workflow** | Step 5 浏览器验证 / Step 6 冒烟 | 串行（修完 bug -> 调本 skill 验证 -> 回 Step 7 文档）|
| **deep-planning-rigorous-validation** | 阶段 7 E2E / 7b UI E2E / 8e 综合验证 | 串行（实现完 -> 调本 skill 验证 -> 回 8b 审计）|
| **team-rigorous-delivery** | Stage 7 Slice E2E / 8e 汇总 | **并行**（每个 Slice SubAgent 各调本 skill -> 回 8e 汇总）|

### 入口契约（上游 -> e2e-excellence）

上游调本 skill 时，需声明：

```yaml
验证目标: bug 修复 | 功能实现 | 契约验证
验证范围:
  - 路由/入口: ['#projects', '#workflow', ...]
  - API 端点: ['/api/v1/plans', ...]
  - 关键交互: ['点击生成规划', '断言错误提示含 model']
预期行为:
  成功路径: "规划生成成功，返回 plan + nodes"
  失败路径: "错误透传，detail.message 含真实原因"
回归测试:
  已有文件: tests/e2e/regression/xxx.regression.spec.js  # 可选
  或要求新建: true
返回位置: fix-bug Step 7 / deep-planning 阶段 8b / team-rigorous Stage 8e
gate_waiver:  # 🆕 v2.2 可选：豁免 pre-existing Gate 失败项（CI 禁用）
  - "Gate 8: 历史文件 benchmark-async.spec.js 弱断言（pre-existing，非本次引入）"
  - "Gate 6: 测试分层进行中（本次只建 regression/）"
```

#### 🆕 v2.2 Gate Waiver 机制

**用途**：当 Gate 失败属于 **pre-existing**（非本次变更引入）时，上游可在入口契约声明 `gate_waiver` 豁免阻塞。

**规则**：
- waiver 必须附**理由**（防滥用，无理由被拒绝）
- waiver 只豁免**阻塞**，不豁免**报告**（仍显示为 WAIVED）
- **CI 模式禁用 waiver**（`CI=true` 时所有 Gate 必须真修）
- waiver 命中 FAIL/WARN -> 状态改 WAIVED，不计入 fail_count

**示例**：
```bash
# 本地豁免 Gate 8（pre-existing 弱断言）
python3 scripts/e2e_doctor.py playwright.config.js \
  --waiver "Gate 8: 历史文件弱断言（pre-existing）"

# 多项豁免（分号分隔）
python3 scripts/e2e_doctor.py playwright.config.js \
  --waiver "Gate 8: 历史弱断言; Gate 5: 本地不需 retries"

# CI 模式（waiver 自动禁用）
CI=1 python3 scripts/e2e_doctor.py playwright.config.js --waiver "Gate 8: ..."  # 无效，仍 BLOCKED
```

**何时用 waiver**：
- ✅ Gate 失败是 pre-existing（git blame 证明非本次引入）
- ✅ 本次验证目标不涉及该 Gate 覆盖的范围
- ❌ 本次变更引入的 Gate 失败（必须真修）
- ❌ CI 环境（禁用）

### 出口契约（e2e-excellence -> 上游）

本 skill 跑完后，返回上游：

```yaml
门禁判定: PASS | PASS_WITH_WARN | BLOCKED
  统计: X PASS / Y WARN / Z FAIL / W WAIVED（10 项 Gate）
  说明:
    - WAIVED = 上游声明豁免的 pre-existing 项（CI 模式下为 0）
    - BLOCKED 仅当 FAIL > 0（WAIVED 不计）
测试结果:
  总数: N
  通过: N-passed
  失败: N-failed
  跳过: N-skipped
证据产物:
  trace: test-results/.../trace.zip
  video: test-results/.../video.webm
  screenshot: test-results/.../*.png
验证报告: memory/<name>-e2e-validation-<date>.md
回归测试: tests/e2e/regression/<name>.regression.spec.js  # 新建或复用
```

### 调用流程（渐进式披露）

```
1. 上游到达 E2E 验证阶段
2. 上游声明"入口契约"（验证目标 + 范围 + 预期行为）
3. 上游显式调用："使用 e2e-excellence skill 跑 E2E 验证"
4. 本 skill 接管，按验证路线执行：
   Step 1: 跑 10 项 Gate 门禁（e2e_doctor.py，支持 --waiver 豁免 pre-existing）
   Step 2-5: 写/复用回归测试（按入口契约）
   Step 6-7: 跑测试 + 收集 trace/video/screenshot
   Step 8: 失败定位（如失败，用 Trace Viewer）
5. 本 skill 输出"出口契约"（门禁判定 + 测试结果 + 证据 + 报告）
6. 上游接回控制权，带着验证结果继续 downstream：
   - fix-bug -> Step 7 文档沉淀（含验证报告链接）
   - deep-planning -> 阶段 8b 需求-实现覆盖度审计
   - team-rigorous -> Stage 8e 跨 Slice 汇总
```

### 上游调用模板（复制即用）

在上游 skill 的 E2E 验证阶段，写：

```markdown
### E2E 验证（渐进式披露到 e2e-excellence）

**入口契约**：
- 验证目标：[bug 修复 / 功能实现 / 契约验证]
- 验证范围：[路由/API/交互列表]
- 预期行为：[成功/失败路径描述]
- 回归测试：[已有文件 or 要求新建]

**调用**：使用 `e2e-excellence` skill 执行验证路线 Step 1-8。

**出口契约（验证后填写）**：
- 门禁判定：[PASS / BLOCKED]
- 测试结果：[X passed / Y failed]
- 证据产物：[trace/video/screenshot 路径]
- 验证报告：[memory 文件路径]

带着结果返回 [上游下一步阶段]。
```

### 并行模式（team-rigorous-delivery 专用）

team-rigorous-delivery 的 Slice E2E 是**并行**的，每个 Slice SubAgent 独立调本 skill：

```
Slice A SubAgent -> e2e-excellence（A 范围）-> 返回 A 结果
Slice B SubAgent -> e2e-excellence（B 范围）-> 返回 B 结果  （并行）
Slice C SubAgent -> e2e-excellence（C 范围）-> 返回 C 结果
                    ↓
            Stage 8e 汇总所有 Slice 结果
```

**并行注意**：
- 每个 SubAgent 用独立的 test-results 子目录（避免产物冲突）
- Gate 1 webServer 配置 `reuseExistingServer: !CI`（多 SubAgent 共享服务）
- 8e 汇总时检查跨 Slice 一致性（入口/响应格式/错误处理）

### 与 verification-before-completion 的关系

上游原有的 `verification-before-completion` 嵌入**保留**（管"证据优先"原则），本 skill 是**其下层的具体执行**：

```
verification-before-completion（原则层：必须证据优先）
        ↓ 具体执行
e2e-excellence（执行层：10 项 Gate + 验证路线 + trace/video）
```

上游 Step 5/阶段 7/Stage 7 仍嵌入 `verification-before-completion`，但执行时**渐进式披露到本 skill** 跑完整验证路线。