// playwright.config.js - E2E Excellence v2.0 配置模板
// 满足 10 项 Gate：直接复制此文件到项目根，改 baseURL 和 webServer.command

import { defineConfig } from 'playwright/test';

const isCI = !!process.env.CI;
const skipAutostart = process.env.V5_E2E_SKIP_AUTOSTART === '1';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: { timeout: 8_000 },   // Gate 9: ≥ 8000ms

  // Gate 5: retries 配置（CI 重试 2 次，本地不重试）
  // 必须配 retries，trace=on-first-retry 才会生成 trace
  retries: isCI ? 2 : 0,

  use: {
    baseURL: process.env.V5_E2E_BASE_URL || 'http://127.0.0.1:8765',
    channel: process.env.V5_E2E_BROWSER_CHANNEL || 'chrome',

    // Gate 2: trace 保留失败现场（on-first-retry = 首次重试时保留）
    trace: 'on-first-retry',

    screenshot: 'only-on-failure',

    // Gate 3: video 保留时序证据
    video: 'retain-on-failure',
  },

  // Gate 1: webServer 自动启停 + Gate 4: reuseExistingServer 适配
  // Gate 10: SKIP_AUTOSTART 兼容开关
  ...(skipAutostart
    ? {}
    : {
        webServer: {
          command: 'bash start.sh start && tail -f .runtime/start/frontend.log',
          url: process.env.V5_E2E_BASE_URL || 'http://127.0.0.1:8765',
          reuseExistingServer: !isCI,   // 本地复用，CI 强制新拉
          timeout: 120_000,
          stdout: 'pipe',
          stderr: 'pipe',
        },
      }),

  // Gate 6: 测试分层目录
  // tests/e2e/{smoke,regression,contract}/*.spec.js
  // 命名: <name>.smoke.spec.js / <name>.regression.spec.js / <name>.contract.spec.js

  reporter: [['list']],

  // CI 模式增强
  ...(isCI ? {
    reporter: [['list'], ['github'], ['html', { open: 'never' }]],
    workers: 1,                // CI 单线程（避免资源竞争）
    forbidOnly: true,          // CI 禁止 .only（防遗漏）
  } : {}),
});