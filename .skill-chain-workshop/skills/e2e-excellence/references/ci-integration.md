# E2E CI 集成

## GitHub Actions

```yaml
# .github/workflows/e2e.yml
name: E2E Tests
on:
  pull_request:
    branches: [main, develop]
  push:
    branches: [main]

jobs:
  e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm

      - name: Install dependencies
        run: |
          npm ci
          npx playwright install --with-deps chromium

      - name: Run e2e_doctor.py gate
        run: python3 skills/e2e-excellence/scripts/e2e_doctor.py playwright.config.js

      # CI=true 触发 webServer 自动启停（reuseExistingServer=false）
      - name: Run E2E tests
        run: CI=1 npx playwright test
        env:
          CI: true

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report-${{ github.run_id }}
          path: |
            test-results/
            playwright-report/
          retention-days: 7

      - name: Upload trace on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-trace-${{ github.run_id }}
          path: test-results/
          retention-days: 30
```

## GitLab CI

```yaml
# .gitlab-ci.yml
e2e:
  stage: test
  image: mcr.microsoft.com/playwright:v1.61.1-jammy
  variables:
    CI: "true"
  before_script:
    - npm ci
    - npx playwright install --with-deps chromium
  script:
    - python3 skills/e2e-excellence/scripts/e2e_doctor.py playwright.config.js
    - CI=1 npx playwright test
  artifacts:
    when: always
    paths:
      - test-results/
      - playwright-report/
    expire_in: 7 days
  retry:
    max: 2
    when:
      - runner_system_failure
      - stuck_or_timeout_failure
```

## CI 模式配置

```js
// playwright.config.js
const isCI = !!process.env.CI;

...(isCI ? {
  reporter: [
    ['list'],
    ['github'],            // GitHub Actions 集成（PR 注释）
    ['html', { open: 'never' }],
  ],
  retries: 2,                // CI 重试 2 次（保证 trace 生成）
  workers: 1,                // CI 单线程（避免资源竞争）
  forbidOnly: true,          // CI 禁止 .only（防遗漏）
} : {
  retries: 0,
  workers: undefined,
})
```

## 10 项 Gate 在 CI 中的执行顺序

```
Step 1: e2e_doctor.py（10 项 Gate 静态检查）
        ↓
Step 2: 冒烟测试（30s-2min）
        ↓
Step 3: 回归测试（2-10min）
        ↓
Step 4: 契约测试（部署前，1-5min）
```

**e2e_doctor.py 必须先于 playwright test**——配置不过关，跑测试也没意义。

```bash
# CI 命令示例
e2e_doctor.py playwright.config.js || exit 1
npx playwright test
```

## SKIP_AUTOSTART 用法

```yaml
# 已有 run-local.sh 时跳过 webServer
- name: Run E2E tests
  run: V5_E2E_SKIP_AUTOSTART=1 npx playwright test
  env:
    V5_E2E_SKIP_AUTOSTART: "1"
```

## 性能优化

### 缓存 Playwright 浏览器

```yaml
- name: Cache Playwright browsers
  uses: actions/cache@v4
  with:
    path: ~/.cache/ms-playwright
    key: ${{ runner.os }}-playwright-${{ hashFiles('package-lock.json') }}
```

### 并行化（按测试文件拆分）

```yaml
strategy:
  matrix:
    shard: [1, 2, 3, 4]
steps:
  - run: npx playwright test --shard=${{ matrix.shard }}/4
```

### 智能跳过（仅变更相关）

```bash
npx playwright test --grep "@smoke"
```

## 失败分析工作流

```
Step 1: 查看 CI 日志
        gh run view ${{ github.run_id }} --log-failed

Step 2: 下载 artifacts
        gh run download ${{ github.run_id }} --name playwright-trace-${{ github.run_id }}

Step 3: 查看 trace
        npx playwright show-trace test-results/*/trace.zip

Step 4: 查看 video
        open test-results/*/video.webm

Step 5: 定位根因
        - Trace Viewer：DOM 状态、网络请求、控制台
        - Video：时序行为、动画进度
        - HTML Report：整体概览、用例依赖
```

## 常见 CI 问题

| 问题 | 根因 | 解决方案 |
|------|------|---------|
| CI 启动超时 | `webServer.timeout: 120_000` 太短 | 加到 300_000（5min） |
| 浏览器未安装 | CI 镜像没 Playwright 浏览器 | `npx playwright install --with-deps` |
| 服务端口冲突 | CI 上有旧进程 | webServer 启动前清理 |
| 假绿（CI 突然全过） | reuseExistingServer=true | `CI=true` 强制新拉 |
| Video 文件过大 | 默认录制完整视频 | 配置 `video.size` |