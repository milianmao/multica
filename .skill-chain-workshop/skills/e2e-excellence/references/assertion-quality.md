# E2E 断言质量 6 项 Checklist

## 6 项 Checklist

### Checklist 1: 不写假阳性断言

```javascript
// ❌ 反模式
expect(true).toBe(true);
expect(page).toBeTruthy();        // 永远 pass
expect(0).toBeLessThan(100);

// ✅ 正例
expect(response.status()).toBe(200);
expect(user.name).toBe('test_user');
expect(items).toHaveLength(3);
```

### Checklist 2: 不依赖 waitForTimeout

```javascript
// ❌ 反模式
await page.waitForTimeout(5000);  // 不可靠
await page.click('button');

// ✅ 正例
await page.waitForSelector('button:has-text("提交")', { state: 'visible' });
await page.waitForResponse('**/api/v1/projects');
await page.waitForLoadState('networkidle');
```

### Checklist 3: 断言具体内容，不只断言存在

```javascript
// ❌ 弱断言
expect(await heading.count()).toBe(1);

// ✅ 强断言
await expect(page.getByRole('heading', { name: '运营总览', level: 1 })).toBeVisible();
```

### Checklist 4: 用语义查询，不用硬编码 selector

```javascript
// ❌ 脆弱
await page.click('.btn-primary.large');
await page.locator('div > ul > li:nth-child(3)').click();

// ✅ 鲁棒
await page.getByRole('button', { name: '提交' }).click();
await page.getByLabel('邮箱').fill('test@example.com');
```

### Checklist 5: API 断言 schema，不只断言 status

```javascript
// ❌ 弱断言
expect(response.status()).toBe(200);

// ✅ 强断言
import { z } from 'zod';
const ProjectSchema = z.object({
  id: z.number(),
  name: z.string(),
  status: z.enum(['active', 'archived']),
});
const body = await response.json();
expect(() => ProjectSchema.parse(body)).not.toThrow();
```

### Checklist 6: 不写"通过即弃"的测试

```javascript
// ❌ 反模式：跳过 = 放弃保护
test.skip();

// ✅ 显式标记已知问题
test.fail('已知 bug #42，待修复');

// ✅ 不阻塞主流程
test('Bug #42 已知回归', async ({ page }) => {
  test.fixme(true, 'Bug #42 待修复');
  // ... 测试代码
});
```

## 5 类常见弱断言

### 弱断言 1: toBeTruthy

```javascript
// ❌ 任何非 null/undefined/false 都 pass
expect(value).toBeTruthy();

// ✅
expect(value).toBe('expected_value');
expect(typeof value).toBe('string');
expect(value).toMatch(/pattern/);
```

### 弱断言 2: toBeDefined

```javascript
// ❌ 任何非 undefined 都 pass（包括 null）
expect(obj).toBeDefined();

// ✅
expect(obj).not.toBeNull();
expect(obj.field).toBeDefined();
```

### 弱断言 3: toBeGreaterThan(0)

```javascript
// ❌ 1 个和 100 个都 pass
expect(items.length).toBeGreaterThan(0);

// ✅
expect(items).toHaveLength(3);
expect(items.length).toBeGreaterThanOrEqual(3);
```

### 弱断言 4: toContain (无前后缀)

```javascript
// ❌ 'error' 或 'no errors' 都 pass
expect(text).toContain('error');

// ✅
expect(text).toMatch(/^Error:/);
expect(text).toBe('Operation failed with error: timeout');
```

### 弱断言 5: waitForTimeout

```javascript
// ❌
await page.waitForTimeout(2000);

// ✅
await page.waitForSelector('.success-message');
await page.waitForResponse('**/api/v1/projects');
```

## selector 鲁棒性 4 个等级

| 等级 | 查询方式 | UI 改版兼容性 |
|------|---------|:------------:|
| ⭐⭐⭐⭐⭐ | `getByRole({ name })` | 完全兼容 |
| ⭐⭐⭐⭐ | `getByLabel({ name })` | 表单结构改版仍兼容 |
| ⭐⭐⭐ | `getByText({ text })` | 文案改版失效 |
| ⭐⭐ | `locator('.class')` | class 改名失效 |
| ⭐ | `locator('#id')` | id 改名失效 |

**目标**：⭐⭐⭐⭐ 及以上占比 ≥ 50%。

## 强断言模板

```javascript
test('完整断言示例', async ({ page, request }) => {
  // Checklist 4: 语义查询
  await page.getByRole('button', { name: /新增项目/ }).click();
  await page.getByLabel('项目名称').fill('my-project');
  
  // Checklist 2: 不靠 sleep，等 API 响应
  const [response] = await Promise.all([
    page.waitForResponse('**/api/v1/projects'),
    page.getByRole('button', { name: /创建并进入项目/ }).click(),
  ]);
  
  // Checklist 5: 响应 schema 断言
  expect(response.status()).toBe(201);
  const body = await response.json();
  expect(body).toMatchObject({
    id: expect.any(Number),
    name: 'my-project',
    status: 'active',
  });
  
  // Checklist 3: UI 内容断言
  await expect(page.getByRole('heading', { name: 'my-project' })).toBeVisible();
});
```

## 自检方法

```bash
# e2e_doctor.py 自动扫描弱断言
python3 skills/e2e-excellence/scripts/e2e_doctor.py playwright.config.js
```

输出示例：
```
[WARN] Gate 8: 断言质量
  ⚠️ 弱断言占比 25.0%（建议 <10%）
  - benchmark-async.spec.js: toBeTruthy×10
  - p0-validation.spec.js: waitForTimeout×2
```

## 指标

| 指标 | 目标 |
|------|:----:|
| 强断言占比 | ≥ 90% |
| 语义查询占比 | ≥ 50% |
| waitForTimeout 使用次数 | ≤ 全套件 5 次 |