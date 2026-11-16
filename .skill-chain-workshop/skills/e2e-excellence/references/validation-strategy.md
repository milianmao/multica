# E2E 验证策略分层

## 3 层验证策略

E2E 测试**按场景分层**，不同场景用不同粒度。

```
┌─────────────────────────────────────────────────┐
│ 契约 (Contract)        最慢 · 最强 · 最深        │
│ 验证跨服务契约（API 响应结构、字段类型）          │
│ 1-5 条核心链路 × 完整契约                         │
│ 跑全量：1-5min                                   │
└─────────────────────────────────────────────────┘
                     ↑
┌─────────────────────────────────────────────────┐
│ 回归 (Regression)      中速 · 中强 · 最专         │
│ 防止已修复 bug 复发                                │
│ 1 个测试 = 1 个历史 bug 的反向断言                │
│ 20-50 条（每个修复的 bug 一条）                   │
│ 跑全量：2-10min                                  │
└─────────────────────────────────────────────────┘
                     ↑
┌─────────────────────────────────────────────────┐
│ 冒烟 (Smoke)          最快 · 最广 · 最浅          │
│ 验证主流程入口可达                                │
│ 不深入验证细节                                    │
│ 5-15 条覆盖主流程                                 │
│ 跑全量：30s-2min                                  │
└─────────────────────────────────────────────────┘
```

## Layer 1: 冒烟测试

**目的**：验证应用没崩、主流程入口可达。

**适用**：
- 每个 PR 必跑（CI 必跑项）
- 部署后立即跑（验证上线没坏）
- 重大变更后跑（验证主流程未受影响）

**特征**：
- 广覆盖（主要入口/路由）
- 浅验证（只验证"可达"，不验证"正确"）
- 快速（单测 < 30s）

**典型用例**：

```javascript
// smoke/main-routes.smoke.spec.js
import { test, expect } from 'playwright/test';

test('冒烟: 主入口可达', async ({ page }) => {
  for (const hash of ['#overview', '#projects', '#requirements']) {
    await page.goto(`/${hash}`);
    await expect(page.locator('body')).not.toBeEmpty();
  }
});

test('冒烟: API 健康检查', async ({ request }) => {
  const r = await request.get('/api/v1/system/capabilities');
  expect(r.status()).toBeLessThan(500);
});
```

## Layer 2: 回归测试

**目的**：防止已修 bug 复发。

**适用**：
- 每个 PR 跑
- Release 前跑
- 长期累积（每个 bug 修复加 1 条）

**特征**：
- 深验证（精确断言 bug 修复的关键行为）
- 可追溯（每条测试对应 1 个历史 bug）

**典型用例**：

```javascript
// regression/bug-26-runtime-registry-onboarding.regression.spec.js
/**
 * Bug #26 回归保护（2026-07-22 修复）
 * 原始 bug：RuntimeRegistry 写操作无引导反馈
 * 修复方案：加 lastNotice state + submit-success 渲染
 */
test('回归: 创建 AgentVersion 后必须有引导反馈', async ({ page }) => {
  await page.goto('/#projects');
  await page.getByRole('button', { name: /新增项目/ }).click();
  // ... 创建项目
  await page.goto('/#agents');
  await page.selectOption('[aria-label="AgentVersion Runtime"]', runtimeId);
  await page.click('button:has-text("创建 AgentVersion")');
  
  // 关键断言：必须有引导反馈（修复前会 fail）
  await expect(page.locator('[role="status"]')).toContainText(/已创建/);
});
```

**规则**：回归测试不能删。它存在的唯一目的就是"防止历史 bug 复发"。

## Layer 3: 契约测试

**目的**：验证跨服务契约稳定（API 响应结构、字段类型、状态码）。

**适用**：
- 跨团队/跨服务
- 第三方 API 集成
- Mock/Stub 外部依赖

**特征**：
- 精确（断言响应字段类型/值）
- 稳定（Mock 外部依赖）

**典型用例**：

```javascript
// contract/projects-api.contract.spec.js
import { test, expect } from 'playwright/test';
import { z } from 'zod';

const ProjectSchema = z.object({
  id: z.number(),
  name: z.string(),
  status: z.enum(['active', 'archived']),
});

test('契约: /api/v1/projects 返回结构稳定', async ({ request }) => {
  const r = await request.get('/api/v1/projects');
  expect(r.status()).toBe(200);
  const body = await r.json();
  
  expect(Array.isArray(body)).toBe(true);
  if (body.length > 0) {
    expect(() => ProjectSchema.parse(body[0])).not.toThrow();
  }
});
```

## 验证策略组合

| 层 | 数量 | 何时跑 | 失败处理 |
|----|:----:|-------|---------|
| 冒烟 | 5-15 条 | 每个 PR | 阻塞合并 |
| 回归 | 20-50 条 | 每个 PR | 阻塞合并 |
| 契约 | 5-10 条 | 部署前 | 阻塞发布 |

总计：30-75 条 → 跑全量 5-15min

## 命名规范

```
tests/e2e/
├── smoke/
│   ├── main-routes.smoke.spec.js
│   └── api-health.smoke.spec.js
├── regression/
│   ├── bug-26-agent-onboarding.regression.spec.js
│   └── bug-23-welcome-history.regression.spec.js
└── contract/
    ├── projects-api.contract.spec.js
    └── runs-api.contract.spec.js
```

## 验证能力指标

| 指标 | 目标 |
|------|:----:|
| 冒烟覆盖率（主流程入口被覆盖比例） | ≥ 90% |
| 回归覆盖率（已修 bug 有回归测试比例） | 100% |
| 契约覆盖率（核心 API 有契约断言比例） | ≥ 80% |
| 运行总时长 | < 15min |

## 何时扩展到 Layer 2/3

| 信号 | 行动 |
|------|------|
| 第 5 个 bug 修复 | 开始建 Layer 2 |
| 第 1 个跨服务集成 | 开始建 Layer 3 |
| 部署上线频繁失败 | Layer 1 必须 100% pass 才能 deploy |
| 第三方 API 变更 | Layer 3 必跑 |

## 反模式

| 反模式 | 后果 |
|--------|------|
| 只有冒烟没有回归 | 已修 bug 复发无人察觉 |
| 回归测试被"清理" | 失去历史 bug 保护 |
| 契约测试不 Mock | 受外部依赖影响，flake 率高 |
| 一锅端全跑 | CI 慢（>30min）+ 维护成本高 |