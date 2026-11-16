---
name: contract-oracle-extractor
version: 0.7.1
description: 解析单一 SDD 契约规范，生成 Contract schema 测试和 contract-oracle-v1 manifest。产物是 schema-only 证据；必须由 ai-quality-engine 的统一业务质量门禁与真实业务验证共同判定，不能独立宣称 code-verified 通过。
evaluated_by:
  - SC-A-001
  - SC-A-002
  - SC-E-001
  - SC-H-002
---

# Skill: contract-oracle-extractor — SDD 契约到 Contract Oracle

## 定位

本 Skill 是生成层：将 `feat-design-planning` 产出的**一个** SDD 契约规范转为 Contract schema 测试和 `oracle-manifest.json`。

```text
输入：docs/vX.Y.Z/契约规范.md（单一文件）
输出：<run>/contract/test_<module>_contract.py
      <run>/contract/oracle-manifest.json
```

生成的测试验证已解析的请求/响应 Schema 与示例负载；它们不是对目标服务的真实调用。因此其证据等级始终是 `schema-only`，不可独立用于 `code-verified` 放行。

## SDD 端点格式

解析器匹配：

```text
^#{3,4}\s+(GET|POST|PUT|DELETE|PATCH)\s+(\S+)\s*(.*)$
```

端点方法必须紧跟在三级或四级 heading 后：

```markdown
### POST /register

**1.1** 用户注册
```

以下格式会导致端点提取为零：

```markdown
### 1.1 POST /register
```

编号应放在正文或单独 heading，不能插在 HTTP 方法前。零端点、空 `contracts`、无效 coverage 会被后续严格门禁 fail-closed 拒绝，而不是解释为“没有需要验证的契约”。

## 独立调试调用

```bash
python3 skills/contract-oracle-extractor/scripts/extract.py \
  --sdd docs/vX.Y.Z/契约规范.md \
  --output-dir tests/oracle/<run-id>/contract \
  --project-root .
```

| 参数 | 含义 |
|---|---|
| `--sdd` | 唯一、非空的 SDD 文件；禁止传 `docs/v*/契约规范.md` glob |
| `--output-dir` | 当前 run 的 `contract/` 目录 |
| `--project-root` | 被验证项目根目录 |

不要在交付流程中单独调用本命令。应由 `ai-quality-engine/scripts/business-quality-gate.py` 统一调用，确保输出目录隔离、pytest 只收集本轮测试，并将当前 SDD/KB/目标代码的 hash 写入最终证据。

## 输出契约

### `test_<module>_contract.py`

每个可解析端点生成 schema 测试；若 SDD 有错误码与 JSON Schema，生成相应参数化断言。测试仅检查文档解析结果与 Schema 负载，不能证明路由已注册、服务真实响应正确或副作用已发生。

### `oracle-manifest.json`

```json
{
  "schema_version": "contract-oracle-v1",
  "source_sdd": "docs/vX.Y.Z/契约规范.md",
  "generated_at": "ISO8601",
  "engine_version": "0.7.0",
  "coverage": { "covered": 2, "total": 2, "ratio": 1.0 },
  "contracts": [
    {
      "endpoint": "POST /register",
      "method": "POST",
      "path": "/register",
      "sdd_section": "§1.1",
      "test_file": "test_register_contract.py",
      "test_function": "test_post_register_schema",
      "status": "covered"
    }
  ]
}
```

`status` 只允许 `covered` 或 `gap`。最终 gate 会为本轮 manifest 补充 `source_sdd_hash`、`source_kb_hash`、`target_code_hash`、`evidence_level` 和 `run_id`；原始 manifest 缺少这些字段时不等于当前代码已验证。

## 与严格门禁的接线

引擎读取最终 run 根目录的 `oracle-manifest.json`：

- `contracts[].status == "gap"` → `O-CONTRACT-GAP`
- `coverage.ratio < 0.80` → `O-CONTRACT-COVERAGE-LOW`
- 空 manifest、错误 schema、空数组、非法 JSON 或 coverage 非法 → fail-closed / `WITHHELD`
- 当前生成测试失败 → 由统一门禁写入 `pytest-results.json`，引擎生成 `O-TEST-FAILURE`

在 `--strict-gate` 下，上述任一业务 finding 都会阻断。生成器不拥有最终 PASS/BLOCK 决定权。

## 工坊链关系

```text
feat-design-planning → 契约规范.md
        ↓
contract-oracle-extractor → schema-only Contract Oracle
        ↓
ai-quality-engine/business-quality-gate.py
        ↓
真实 public entry 验证 + 严格判定
```

它与 `scenario-oracle-builder` 互补：本 Skill 覆盖契约解析和 Schema，后者覆盖 KB 业务流。两者都不能替代项目本身的真实入口验证。

## 版本

- `0.7.1`：澄清单一 SDD 输入、schema-only 边界与统一业务质量门禁接线；禁止将生成的 Schema 测试宣传为真实 code-verified 验证。
- `0.7.0`：初版。
