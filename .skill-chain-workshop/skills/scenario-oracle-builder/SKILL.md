---
name: scenario-oracle-builder
version: 0.7.1
description: 解析 KB 核心业务流目录，生成 Scenario 解析测试和 scenario-oracle-v1 manifest。产物默认是 schema-only 证据；必须进入 ai-quality-engine 的统一业务质量门禁并配合真实公开入口验证，不能将 Mock 或文档解析测试称为真实集成验证。
evaluated_by:
  - SC-A-003
  - SC-A-004
  - SC-E-002
  - SC-H-003
---

# Skill: scenario-oracle-builder — KB 业务流到 Scenario Oracle

## 定位

本 Skill 是生成层：读取知识库中的核心业务流 Markdown，生成 Scenario 解析测试与 `scenario-manifest.json`。

```text
输入：docs/project-note/03-核心数据业务流/（目录）
输出：<run>/scenario/test_<flow>_scenario.py
      <run>/scenario/scenario-manifest.json
```

生成测试用于验证业务流文档能否被完整解析，并记录正常、异常、边界分支覆盖。它们可能使用 schema 负载或 Mock，因此默认只属于 `schema-only` 证据；不得描述为真实 API integration、真实数据验证或业务通过。

## KB 业务流格式

目录中每个非 README Markdown 文件应描述一个业务流。解析器需要一级标题和以下二级小节：

```markdown
# 用户注册

## 触发
- 未登录用户提交 username、password、email

## 数据流
- 客户端请求 → 注册入口 → 写入用户记录 → 返回 201

## 异常处理
- 重复 username → 409 USER_EXISTS

## 边界
- username 长度为 1 到 32

## Given-When-Then
```gherkin
Given 一个未注册的 username "alice"
When 用户提交注册请求
Then 返回 201
And 新增一条用户记录
```
```

**必需小节（covered 判据）**：`触发点`、`数据流`、`异常处理` 三项齐全即 `covered`。缺少任一必需小节会记录为 `partial_parse`；不能把它当作已覆盖业务流。`README.md` 不作为业务流输入。

**边界/GWT 为增强字段**：`边界情况` / Given-When-Then 独立小节缺失不降级。旧模板把边界场景（降级 / 超时 / 冲突 / 不可用 / 不可变状态等）折叠进「异常处理」段落，parser 会把这些行识别进 `boundary_cases` 以提升测试覆盖精度，但不改变 covered 判据。

## 独立调试调用

```bash
python3 skills/scenario-oracle-builder/scripts/extract.py \
  --kb docs/project-note/03-核心数据业务流/ \
  --output-dir tests/oracle/<run-id>/scenario \
  --project-root .
```

| 参数 | 含义 |
|---|---|
| `--kb` | 核心业务流目录；交付链路不接受虚构的 `03-核心数据业务流.md` |
| `--output-dir` | 当前 run 的 `scenario/` 目录 |
| `--project-root` | 被验证项目根目录 |

解析器本身也支持单个 KB 文件用于局部调试；工坊交付链路必须传目录，确保本轮所有核心业务流都参与 hash、manifest 与 coverage 计算。

不要在交付流程中单独调用本命令。应由 `ai-quality-engine/scripts/business-quality-gate.py` 调用，使生成测试与历史 `tests/scenario/` 隔离，并把当前 KB/SDD/目标代码 hash 绑定到 run。

## 输出契约

### `test_<flow>_scenario.py`

每个业务流生成正常、异常、边界三个解析场景。它们仅证明抽取出的文档场景和生成断言成立；要证明当前系统行为，必须由 `--verify-command` 通过真实 CLI、HTTP、app factory 或公开模块入口执行业务断言。

### `scenario-manifest.json`

```json
{
  "schema_version": "scenario-oracle-v1",
  "source_kb": "docs/project-note/03-核心数据业务流/",
  "generated_at": "ISO8601",
  "engine_version": "0.7.0",
  "coverage": {
    "covered_flows": 1,
    "total_flows": 1,
    "ratio": 1.0
  },
  "fork_coverage": { "normal": 1, "error": 1, "boundary": 1 },
  "mock_ceiling_risk": false,
  "flows": [
    {
      "flow_name": "用户注册",
      "kb_doc": "用户注册.md",
      "status": "covered"
    }
  ]
}
```

`flows[].status` 允许 `covered`、`partial_parse`、`unparseable`。严格判定中只有 `covered` 放行，其余每一项都产生 `O-SCENARIO-GAP`。最终 gate 会补充当前输入 hash、`evidence_level` 与不可复用 `run_id`；原始生成文件不能为之后的代码变更背书。

## 与严格门禁的接线

引擎读取最终 run 根目录的 `scenario-manifest.json`：

- `flows[].status != "covered"` → `O-SCENARIO-GAP`
- `coverage.ratio < 0.90` → `O-SCENARIO-COVERAGE-LOW`
- `mock_ceiling_risk == true` → `O-MOCK-CEILING`
- 空 manifest、错误 schema、未知 status、空 flows 或非法 JSON → fail-closed / `WITHHELD`
- 当前生成测试失败 → 统一门禁写入 `pytest-results.json`，引擎生成 `O-TEST-FAILURE`

不存在 `O-GWT-MISSING` 规则。GWT 缺失由生成器记录为 `partial_parse`，再由 `O-SCENARIO-GAP` 阻断。

## 工坊链关系

```text
knowledge-base-builder → docs/project-note/03-核心数据业务流/
        ↓
scenario-oracle-builder → schema-only Scenario Oracle
        ↓
ai-quality-engine/business-quality-gate.py
        ↓
真实业务断言 + 严格 PASS/BLOCK
```

本 Skill 不替代 `contract-oracle-extractor`、`test-pyramid-builder` 或项目的真实 E2E/集成测试。它的职责是把 KB 业务流转为可追踪的 Oracle 候选，不是替项目模拟真实运行时。

## 版本

- `0.7.1`：修正 KB 目录输入、`flows` schema 和 GWT 规则说明；明确生成测试的 schema-only 边界与真实验证要求。
- `0.7.0`：初版。
