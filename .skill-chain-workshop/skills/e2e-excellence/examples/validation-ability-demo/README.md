# 验证能力示例

## 文件

- `validation-ability.spec.js` - 覆盖 4 项验证能力 Gate 的可复用测试

## 用途

演示如何写满足 e2e-excellence v2.0 Gate 6-8 的测试：

| Gate | 测试覆盖 |
|------|---------|
| Gate 6 验证策略分层 | 演示冒烟测试 + 回归测试写法 |
| Gate 7 selector 鲁棒性 | 用 `getByRole` / `getByLabel`（场景 4） |
| Gate 8 断言质量 | 用 `toContainText` 语义断言（场景 1+3, 5） |

## 测试覆盖

| 测试 | 验证能力 |
|------|---------|
| 场景 1+3: 回归测试模板 | 防止历史 bug 复发 |
| 场景 2: 失败定位（故意 fail） | 验证 trace + video 生成 |
| 场景 4: selector 鲁棒性 | `getByRole` + `getByLabel` |
| 场景 5: AI 兜底概念 | 动态 UI 文本匹配 |
| 综合: 冒烟测试模板 | 主流程入口可达 |

## 用法

```bash
# 复制到你的项目
cp validation-ability.spec.js tests/e2e/regression/

# 跑测试
npx playwright test tests/e2e/regression/validation-ability.spec.js --retries=2

# 验证 Gate
python3 skills/e2e-excellence/scripts/e2e_doctor.py playwright.config.js
```

## 注意

**本案例是验证能力模板**，不是 bug 修复流程。bug 修复请用 `fix-bug-workflow`。

## 相关文档

- [SKILL.md](../../SKILL.md) - e2e-excellence 入口
- [references/validation-strategy.md](../../references/validation-strategy.md) - 3 层验证策略
- [references/assertion-quality.md](../../references/assertion-quality.md) - 断言质量 6 项 checklist