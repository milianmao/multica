// Midscene.js + Playwright 实战集成示例
// 场景：v5 workspace 8 环节闭环测试
// 策略：混合模式（90% selector + 10% AI 兜底）

import { chromium } from 'playwright';
import Midscene from '@midscene/web';
import { test, expect } from 'playwright/test';

test.describe('v5 workspace 8 环节闭环（含 AI 兜底）', () => {
  let browser, page, midscene;

  test.beforeAll(async () => {
    browser = await chromium.launch();
    page = await browser.newPage();
    midscene = new Midscene.Agent(page);
  });

  test.afterAll(async () => {
    await browser.close();
  });

  // 环节 1-2: 创建项目（标准表单，selector 即可）
  test('环节 1: 创建项目', async () => {
    await page.goto('http://127.0.0.1:8765/#projects');
    await page.click('button:has-text("新增项目")');
    await page.fill('input[name="name"]', 'demo-ai-project');
    await page.fill('textarea[name="description"]', 'AI 增强测试项目');
    await page.click('button:has-text("创建并进入项目")');
    await expect(page.locator('text=demo-ai-project')).toBeVisible();
  });

  // 环节 3: 创建 AgentVersion（混合模式：selector + AI 兜底）
  test('环节 3: 创建 AgentVersion（含 AI 兜底）', async () => {
    await page.goto('http://127.0.0.1:8765/#agents');

    // 稳定路径：用 selector（快、便宜、100% 准确）
    await page.selectOption('select[aria-label="AgentVersion Runtime"]', { label: 'TestAgent · Codex' });
    await page.fill('input[aria-label="Agent Key"]', 'coding-center-planner');
    await page.fill('input[aria-label="显示名称"]', 'Coding Center Planner');
    await page.fill('input[aria-label="版本"]', '1.0.0');
    await page.click('button:has-text("创建 AgentVersion")');

    // ✅ AI 兜底：等待成功引导通知（动态 Toast）
    //   传统 selector 可能错过（不同浏览器渲染差异）
    //   用 Midscene.js aiAssert 自然语言断言
    await midscene.aiAssert('页面顶部显示成功引导"AgentVersion 已创建（draft）"');
  });

  // 环节 5: 提交需求（标准表单 + AI Modal 处理）
  test('环节 5: 提交需求（含 Modal AI 处理）', async () => {
    await page.goto('http://127.0.0.1:8765/#requirements');

    // 稳定路径：selector 填表单
    await page.click('button[role="tab"]:has-text("自然语言")');
    await page.fill('textarea[name="description"]', '实现用户登录功能');
    await page.click('button:has-text("提交并交给 Planner")');

    // 动态 Modal：规格审核弹窗（结构不稳定 → AI 处理）
    // ❌ 不推荐：await page.waitForSelector('.spec-review-modal')  // 脆弱
    // ✅ 推荐：AI 自然语言处理
    await midscene.aiAction('在审核意见输入框中填写"通过"');
    await midscene.aiAction('点击"审核通过，开始规划"按钮');

    // 结构化数据提取（Stagehand extract 模式）
    const planInfo = await midscene.aiQuery('当前页面的 Plan ID 是什么？');
    expect(planInfo).toMatch(/plan-\d+/);
  });

  // 环节 6-7: 规划与执行（混合模式）
  test('环节 6-7: 规划审核 + 执行', async () => {
    await page.goto('http://127.0.0.1:8765/#workflow');

    // 标准按钮：selector
    await page.click('button:has-text("批准 DAG 并进入执行")');

    // 跳转等待 + Toast 提示（动态 UI → AI 断言）
    await midscene.aiWaitFor('看到"全部节点完成"或"提交验收"按钮');

    // 数据提取
    const deliveryStatus = await midscene.aiQuery('当前交付状态是什么？');
    expect(deliveryStatus).toMatch(/pending_review|merged/);
  });

  // 环节 8: 验收合并
  test('环节 8: 验收合并', async () => {
    await page.goto('http://127.0.0.1:8765/#delivery');

    // 标准按钮：selector
    await page.click('button:has-text("批准并合并到 main")');

    // 成功提示（动态 Toast → AI 断言）
    await midscene.aiAssert('页面显示"合并成功"消息');
  });
});

/* ===========================================
   关键设计原则
   ===========================================

   ✅ 稳定路径 → Playwright selector（快、便宜、100% 准确）
     - 表单填写（input/select/textarea）
     - 标准按钮（固定文案/role）
     - 路由跳转（#projects / #agents 等）

   ✅ 动态 UI → Midscene.js AI（容错高、适配变更）
     - Modal 弹窗（结构不稳定）
     - Toast 通知（动态出现/消失）
     - 数据提取（自然语言查询）

   ✅ 数据断言 → Stagehand extract API
     - 提取结构化数据（如 Plan ID、状态）
     - 用 Zod schema 验证

   ❌ 避免：全用 AI
     - 成本高（每操作 $0.01-0.05）
     - 慢（每操作 2-5s）
     - 脆弱（prompt 不稳定）

   ❌ 避免：AI 处理路由跳转
     - selector 100% 准确且快

   =========================================== */