# AI 工具选择决策树

## 决策流程

```
Q1: 项目语言？
  ├─ 中文 → Midscene.js（主推）
  └─ 英文 → Q2

Q2: 测试规模？
  ├─ <100 行（小型）→ Vouch
  ├─ 100-500 行（中型）→ Midscene.js
  └─ >500 行（大型）→ Midscene.js + Stagehand 混合

Q3: AI 代理集成（Claude Code / MCP）？
  ├─ 是 → Slapwright
  └─ 否 → Q4

Q4: 测试场景？
  ├─ 探索性测试（新功能快速验证）→ Vouch
  ├─ 复杂交互（Modal/动画）→ Stagehand
  ├─ 视觉回归（截图对比）→ Midscene.js
  └─ 现有测试改造（增量集成）→ Midscene.js
```

## 决策矩阵

| 项目特征 | 主推 | 备选 | 避免 |
|---------|------|------|------|
| 中文 + >500 行 Playwright | Midscene.js | Stagehand | Vouch |
| 英文 + <100 行测试 | Vouch | Midscene.js | Slapwright |
| AI 代理集成 | Slapwright | Midscene.js | Vouch |
| 零 AI 预算 | 不用 AI | — | — |
| 高视觉元素 + 频繁 UI 变更 | Vouch | Midscene.js | 传统 selector |
| 复杂 Modal/动画 | Stagehand | Midscene.js | 传统 selector |
| 视觉回归 | Midscene.js | Vouch | Stagehand |
| 纯后端 API（无 UI） | 不用 E2E | — | 任何 AI 工具 |

## 工具适用场景

### Midscene.js

- 中文项目的**主推选择**
- 现有 Playwright 测试**增量集成**（不破坏）
- 动态 UI（Modal/Toast/动画）的断言兜底
- 中文 prompt 稳定

### Stagehand

- 复杂交互场景（Modal、动画、动态加载）
- 结构化数据提取（订单列表、用户信息）+ Zod schema
- 按需启用（混合模式）

### Vouch

- 探索性测试（新功能快速验证）
- 视觉元素为主（设计稿、营销页、文档站）
- UI 频繁改版（零选择器零维护）
- **不推荐用于严格 CI**（80-90% 准确率不够）

### Slapwright

- AI 代理自动化（Claude Code / MCP 集成）
- 轻量级浏览器交互（CLI 命令）
- **不推荐用于完整 E2E 套件**

## 渐进式引入策略

### 第 1 周：保留传统（零风险）
- 套用 e2e-excellence v2.0 的 10 项 Gate
- 现有测试不动

### 第 2-4 周：试点 AI 增强
- 选 1-2 个**脆弱 selector** 测试
- 用 Midscene.js aiAction 替代
- 对比：成本/延迟/准确率

### 第 5-8 周：扩展到关键场景
- Modal/动画测试 → Stagehand act()
- 数据提取 → Stagehand extract()
- 视觉回归 → Midscene.js

### 第 9 周+：全面评估
- 收集数据：成本/效率/维护成本
- 决策：是否继续推广 / 回退到传统

**原则**：**永远保留传统 selector 作为兜底**，AI 是增强不是替代。

## 反模式

| 反模式 | 后果 |
|--------|------|
| 全用 AI 替代 selector | 成本高、慢、脆弱 |
| Vouch 用于严格 CI | 视觉模型准确率不够 |
| Slapwright 用于完整 E2E | 功能不够 |
| Stagehand 用于视觉回归 | 不擅长视觉 diff |
| AI 工具用于纯后端 | 无 UI 可操作 |
| 没混用：直接用 AI 重写 760 行测试 | 迁移成本远大于收益 |

## 工具评估清单

采购/选型时给每个工具打 5 个维度分（满分 25）：

| 维度 | 权重 |
|------|------|
| 中文支持 | 5 |
| 集成成本 | 5 |
| 单操作成本 | 5 |
| 准确率 | 5 |
| AI 代理友好 | 5 |

**本仓库 v5 workspace 评分示例**：
- Midscene.js：5+4+2+3+2 = **16/25** ⭐⭐⭐⭐⭐ 主推
- Stagehand：3+3+3+4+2 = **15/25** ⭐⭐⭐⭐
- Slapwright：3+5+4+2+5 = **19/25** ⭐⭐⭐⭐⭐（MCP 场景）
- Vouch：3+2+1+2+2 = **10/25** ⭐⭐（避免）