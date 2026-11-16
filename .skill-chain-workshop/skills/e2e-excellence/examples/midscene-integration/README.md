# Midscene.js 集成示例

## 文件

- `8-link-flow-with-ai.spec.js` - 8 环节闭环 + AI 兜底的完整示例

## 用途

演示混合模式（90% selector + 10% AI）的写法，对应 SKILL.md 第 5 节 "AI 增强工具"。

## 集成步骤

### 1. 安装依赖

```bash
npm install --save-dev @midscene/web playwright
```

### 2. 配置 VLM API Key

```bash
# .env.test（不要 commit）
MIDSCENE_VLM_API_KEY=sk-xxx
MIDSCENE_VLM_MODEL=qwen-vl-max  # 推荐中文项目用 Qwen-VL-Max
```

### 3. 跑测试

```bash
npx playwright test 8-link-flow-with-ai.spec.js
```

## 关键设计

| 场景 | 工具 | 理由 |
|------|------|------|
| 标准表单 | Playwright selector | 快、准、便宜 |
| Modal 弹窗 | Midscene.js `aiAction` | 容错高、适配 UI 变更 |
| Toast 通知 | Midscene.js `aiAssert` | 动态 UI，传统 selector 脆弱 |
| 数据提取 | Midscene.js `aiQuery` | 自然语言查询 |
| 路由跳转 | Playwright selector | 100% 准确 |

## 成本估算

| 环节 | selector 操作 | AI 操作 | 单次成本 | 总成本 |
|------|:------------:|:------:|:--------:|:------:|
| 8 环节闭环 | 15 | 7 | - | $0.14 |

## 相关文档

- [SKILL.md](../../SKILL.md) 第 5 节 "AI 增强工具"
- [references/ai-tools-comparison.md](../../references/ai-tools-comparison.md) - 4 工具对比
- [references/environment-setup.md](../../references/environment-setup.md) - 环境依赖