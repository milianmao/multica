---
name: ai-quality-engine
version: 0.7.5
description: 本地自包含的 ai-quality 0.7.5 判定引擎与唯一业务质量门禁。统一入口从当前唯一 SDD、KB 业务流目录和目标代码生成隔离证据，要求真实业务验证，并以严格 gate 输出唯一 PASS/BLOCK。禁止把 schema/Mock 生成测试当作 code-verified 通过。
evaluated_by:
  - SC-A-005
  - SC-E-003
  - SC-H-005
  - OR-R-001
---

# Skill: ai-quality-engine — 业务质量严格门禁

## 定位

本 Skill 提供两层能力：

1. `ai_quality/` 是随 Skill 分发的本地判定引擎，消费 Oracle manifest、pytest 汇总和目标代码，产出 `O-*` finding；
2. `scripts/business-quality-gate.py` 是工坊链唯一允许用于交付的业务质量门禁。它负责生成当前证据、运行当前生成的测试、要求真实业务验证，并调用引擎严格落锤。

```text
KB 业务流目录 + 唯一 SDD + 当前业务入口代码
                  │
                  ▼
business-quality-gate.py
  ├─ Contract Oracle（本轮隔离目录）
  ├─ Scenario Oracle（本轮隔离目录）
  ├─ 仅运行本轮 test_*.py → pytest-results.json
  ├─ 真实 public entry 验证 → business-verification.json
  └─ ai-quality --profile code-verified --strict-gate
                  │
                  ▼
             PASS (0) / BLOCK (3)
```

生成器、pytest 或引擎中任一层单独成功，都不是业务质量通过。交付门禁不得再手工复制 manifest、扫描历史 `tests/scenario/`、人工解释多个 JSON 后决定放行。

## 自包含引擎与同步边界

- 引擎版本：`0.7.5`；运行包位于 `ai_quality/`，依赖 Python ≥3.9 与 `pydantic>=2`。
- 门禁一律使用本仓入口：`python3 skills/ai-quality-engine/scripts/ai-quality-check.py`。禁止使用机器上可能指向其他项目的全局 `ai-quality` CLI。
- 逻辑真源为 `evaluate-ai-code-service/backend/`；其离线闭包通过受管清单同步到本 Skill。发布或验收前必须运行源仓 `scripts/sync_offline.py --check`，不允许手工修补某一个镜像。

```bash
cd skills/ai-quality-engine
python3 -m ai_quality --version
# 0.7.5
```

## 唯一门禁调用

在项目根目录执行。`--sdd` 必须是一个明确的当前版本文件，不能使用会展开为多个文件的 `docs/v*/契约规范.md`。`--kb` 必须是核心业务流目录，不是虚构的单一 `03-核心数据业务流.md`。

```bash
python3 skills/ai-quality-engine/scripts/business-quality-gate.py \
  --project-root . \
  --sdd docs/vX.Y.Z/契约规范.md \
  --kb docs/project-note/03-核心数据业务流/ \
  --target path/to/current_business_entry.py \
  --output-dir tests/oracle \
  --run-id <本轮不可复用ID> \
  --evidence-level code-verified \
  --verify-command "python3 tests/verify_current_business_flow.py"
```

| 参数 | 约束 |
|---|---|
| `--sdd` | 一个非空 SDD 文件；应是本轮实际采用的契约规范 |
| `--kb` | 含非 README Markdown 业务流的目录 |
| `--target` | 一个或多个本轮变更涉及的真实业务入口源文件 |
| `--output-dir` | 证据根目录；每次实际证据写入 `<output-dir>/<run-id>/` |
| `--run-id` | 字母、数字、点、下划线或短横线；已存在即拒绝，防止重放旧证据 |
| `--evidence-level` | `schema-only` 或 `code-verified` |
| `--verify-command` | 仅 `code-verified` 必填；传入无 shell 的可执行命令及参数 |

本轮目录固定包含：

```text
<output-dir>/<run-id>/
├── contract/
│   ├── test_*_contract.py
│   └── oracle-manifest.json
├── scenario/
│   ├── test_*_scenario.py
│   └── scenario-manifest.json
├── oracle-manifest.json
├── scenario-manifest.json
├── pytest-results.json
├── business-verification.json
└── gate-result.json
```

`business-verification.json` 只由真实验证命令写入当前 run 目录；不可预先放置、不可从外部路径复制。门禁向验证器提供：

```text
BUSINESS_QUALITY_RUN_ID
BUSINESS_QUALITY_VERIFY_EVIDENCE
BUSINESS_QUALITY_SOURCE_SDD_HASH
BUSINESS_QUALITY_SOURCE_KB_HASH
BUSINESS_QUALITY_TARGET_CODE_HASH
```

## 证据层级与真实验证契约

### `schema-only`

Contract/Scenario 生成器验证的是 SDD/KB 解析、Schema 和生成测试，不足以证明目标业务代码符合规则。即使 pytest 全绿且引擎没有 finding，`schema-only` 也固定输出 `BLOCK`（exit `3`）。它只可用于发现解析或覆盖缺口，不能作为交付放行依据。

### `code-verified`

`--verify-command` 必须通过真实的公开入口验证当前行为：CLI、HTTP、app factory 或公开模块入口任选其一。Mock 返回值、仅检查进程成功、`true`、空命令和只回显 hash 都不是业务验证。

验证器必须在 `$BUSINESS_QUALITY_VERIFY_EVIDENCE` 写入：

```json
{
  "schema_version": "business-verification-v1",
  "run_id": "<BUSINESS_QUALITY_RUN_ID>",
  "source_sdd_hash": "<BUSINESS_QUALITY_SOURCE_SDD_HASH>",
  "source_kb_hash": "<BUSINESS_QUALITY_SOURCE_KB_HASH>",
  "target_code_hash": "<BUSINESS_QUALITY_TARGET_CODE_HASH>",
  "assertions": [
    {
      "name": "订单从待支付迁移为已支付",
      "type": "state-transition",
      "passed": true
    }
  ]
}
```

`assertions` 必须非空，并且至少有一条 `passed: true` 的业务断言。允许的类型是：`return-value`、`error-code`、`state-transition`、`side-effect`。run id 或任一输入 hash 不匹配、JSON 无效、验证命令失败，均为 `BLOCK`。

## 严格判定与退出码

统一入口的结论只读取 `gate-result.json.gate.verdict` 和进程退出码：

| 结果 | 退出码 | 含义 |
|---|---:|---|
| `PASS` | `0` | 当前 run 的生成、pytest、真实验证和严格引擎判定全部通过 |
| `BLOCK` | `3` | 任一 extractor、pytest、真实验证、manifest、严格 finding 或证据层级不符合要求 |
| 参数错误 | `2` | SDD/KB/target/run id 等输入不合法 |

内部引擎的 `--strict-gate` 会在以下情形阻断：`aggregate_status != valid`、缺 code-verified 证据、`O-CONTRACT-GAP`、`O-SCENARIO-GAP`、`O-MOCK-CEILING`、`O-CONTRACT-COVERAGE-LOW`、`O-SCENARIO-COVERAGE-LOW`、`O-TEST-FAILURE`。Critical 业务 finding 不得被总分或人工说明抵消。

如需排查引擎本身，可对某个已生成的 run 目录执行低层命令；这不是交付入口：

```bash
python3 skills/ai-quality-engine/scripts/ai-quality-check.py \
  check path/to/current_business_entry.py \
  --profile code-verified \
  --oracle-dir tests/oracle/<run-id> \
  --strict-gate \
  --format json
```

## 与工坊链的职责边界

```text
knowledge-base-builder → docs/project-note/03-核心数据业务流/
feat-design-planning   → docs/vX.Y.Z/契约规范.md
contract/scenario      → schema-only Oracle 生成
ai-quality-engine      → 当前证据编排、严格判定、唯一 PASS/BLOCK
  ├─ deep-planning 阶段 8f
  └─ fix-bug Step 6 Phase E
```

- `contract-oracle-extractor` 与 `scenario-oracle-builder` 不得自行宣布 `code-verified PASS`。
- `deep-planning` 和 `fix-bug` 只能调用本 Skill 的统一入口，不得各自重建生成、pytest 和引擎判定流程。
- 缺少真实入口 adapter 的项目只能保留 `schema-only`/`BLOCK`，不得把静态或 Mock 覆盖宣传为真实业务通过。

## 版本

- `0.7.5`：新增统一 `business-quality-gate.py`；证据按不可复用 run 隔离；仅执行本轮生成测试；将 pytest、SDD/KB/目标代码 hash 与真实业务验证绑定；`schema-only` 强制 BLOCK；统一调用 `--strict-gate` 输出机器可读 PASS/BLOCK。
- `0.7.4`：门禁入口改为本项目 Skill 自带引擎，避免全局 pip CLI 指向错误副本。
- `0.7.3`：修复 scenario manifest `flows`/状态值 drift；仅 `covered` 放行。
