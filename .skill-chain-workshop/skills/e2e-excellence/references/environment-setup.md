# E2E 环境依赖

## 基础环境（所有场景必需）

| 依赖 | 版本 | 说明 |
|------|------|------|
| Node.js | ≥ 18 | 推荐 20 LTS |
| Chrome / Edge | 最新版 | Playwright 通过 channel: 'chrome' 调用系统 Chrome |
| Python | ≥ 3.8 | 仅 Vouch 需要 |

## Playwright 浏览器

```bash
npx playwright install chromium
```

## AI 增强工具（按需）

| 工具 | 额外依赖 | 推荐模型 |
|------|---------|---------|
| Midscene.js | VLM API | Qwen-VL-Max（中文友好） |
| Stagehand | LLM API | GPT-4o / Claude-3.5-Sonnet |
| Vouch | VLM API | GPT-4-Vision / Claude-3-Opus |
| Slapwright | 无（纯 CDP） | — |

## 环境变量模板

```bash
# .env.test（不要 commit）
# ===== Midscene.js =====
DASHSCOPE_API_KEY=sk-xxx
MIDSCENE_VLM_API_KEY=${DASHSCOPE_API_KEY}
MIDSCENE_VLM_MODEL=qwen-vl-max
MIDSCENE_VLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# ===== Stagehand =====
STAGEHAND_LLM_API_KEY=${DASHSCOPE_API_KEY}
STAGEHAND_LLM_MODEL=qwen-vl-max

# ===== Playwright =====
V5_E2E_BASE_URL=http://127.0.0.1:8765
V5_E2E_BROWSER_CHANNEL=chrome

# ===== 兼容旧 run-local.sh =====
V5_E2E_SKIP_AUTOSTART=1   # 跳过 webServer 自动启停
```

## 安装命令

### 基础依赖

```bash
# macOS
brew install node@20
brew install --cask google-chrome
npx playwright install chromium

# Ubuntu
sudo apt install -y nodejs npm google-chrome-stable
sudo npx playwright install-deps chromium
```

### AI 工具（按需）

```bash
# Midscene.js
npm install --save-dev @midscene/web @midscene/core

# Stagehand
npm install --save-dev @browserbasehq/stagehand zod

# Vouch
npm install -g vouch
# 或 Python
pip install vouch

# Slapwright
npm install -g slapwright
```

## 一键安装脚本

```bash
#!/bin/bash
# install-e2e.sh

set -euo pipefail

# 1. 核心依赖
npm install --save-dev \
  playwright \
  @playwright/test

# 2. 浏览器
npx playwright install chromium

# 3. AI 工具（按需）
# npm install --save-dev @midscene/web @midscene/core
# npm install --save-dev @browserbasehq/stagehand zod

# 4. 验证
node --version
npx playwright --version
```

## 常见环境问题

### Chrome 未找到

```bash
# macOS
export CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Linux
export CHROME_PATH=/usr/bin/google-chrome-stable

# 或在 playwright.config.js 中指定
use: { channel: 'chrome' }  // 强制使用系统 Chrome
```

### VLM API 限流

```javascript
const agent = new Midscene.Agent(page, {
  retry: 3,
  retryDelay: 2000,
  timeout: 30000,
});
```

### 中文 prompt 稳定性

```bash
# 推荐：Qwen-VL（中文优化）
MIDSCENE_VLM_MODEL=qwen-vl-max

# 不推荐：GPT-4-Vision（中文弱）
# MIDSCENE_VLM_MODEL=gpt-4-vision-preview
```

## VLM 模型对比

| 提供商 | 模型 | 中文支持 | 单次成本 | 推荐场景 |
|--------|------|:--------:|:--------:|---------|
| 阿里 Qwen | qwen-vl-max | ✅ 强 | ¥0.02-0.05 | **中文项目首选** |
| 阿里 Qwen | qwen-vl-plus | ✅ 强 | ¥0.005-0.01 | 成本敏感 |
| OpenAI | gpt-4o | ⚠️ 弱 | $0.01-0.03 | 英文项目 |
| Anthropic | claude-3.5-sonnet | ✅ 强 | $0.015-0.04 | 备选 |

## 环境检查清单

- [ ] Node.js ≥ 18
- [ ] Chrome 最新版
- [ ] `npx playwright install chromium` 完成
- [ ] VLM API Key 配置（按需）
- [ ] `.env.test` 文件就位
- [ ] 网络可访问 VLM API（按需）