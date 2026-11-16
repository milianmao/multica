---
name: test-pyramid-builder
version: 1.3
description: Use when you have finished fixing bugs in a project and need to build automated tests to prevent regression, or when onboarding to a project that lacks test coverage. Also use when the user asks to "build a test pyramid", "add tests after bugfixes", or "沉淀测试". v1.3 adds Step 6 逆向验证（mutation testing 思路：破坏代码看测试能否发现，防止"测试糊弄"+ AI 声明完成但业务不通）. v1.2 adds select_tests.py unified test selection API (single source of truth for deep-planning stage 7.0 + fix-bug Step 3, D6 fix). v1.1 adds business flow mapping, mock ceiling detection, fork-path coverage, and L1.5 auto-trigger.
evaluated_by:
  - SC-A-005
  - SC-E-005
  - SC-H-004
  - SC-H-005
  - TP-M-001
---

# Test Pyramid Builder v1.3

## Overview

After fixing bugs, extract the fix patterns into layered automated tests so those bugs never come back. Build from the bottom up: shell smoke → API integration → component unit → logic unit.

**v1.1 新增**: 业务流映射（Step 0）、Mock 天花板检测、分叉路径覆盖检查、L1.5 自动触发。

**Core principle:** Every bug fix leaves behind a test that would have caught it.

## When to Use

- Finished a bugfix session and want to prevent regression
- Onboarding to a project with no test coverage
- User asks for "测试金字塔", "test pyramid", "沉淀测试"
- User says "这些修复以后怎么自动验证"

**Skip when:** Project already has comprehensive tests covering the fixed area, or the fix is purely configuration (env vars, deployment settings).

## The 6-Layer Pyramid

```
        ╱  L0  E2E Browser       ╲     Playwright: 真实浏览器, 前后端全链路
       ╱   L1.5 Service Integration ╲   真实 API 调用, 多服务联调, 无 Mock
      ╱    L2b  Mock Integration     ╲   Mock 外部依赖, 验证核心 Pipeline
     ╱     L2a  Route + Model-DB     ╲   路由注册 + ORM 列 vs 数据库列
    ╱      L3   Component Unit       ╲   mount 组件 → 断言 DOM
   ╱       L4   Logic Unit           ╲   纯函数: input → output
  ──────────────────────────────────────
```

| Layer | 测什么 | 运行时间 | Mock? | 谁来写 |
|-------|--------|:------:|:-----:|--------|
| **L0 E2E Browser** | 真实浏览器, 前后端全链路: 登录→操作→断言UI | 30-120s | 无 | 1-2 条核心用户旅程 |
| **L1 Shell Smoke** | 每个端点返回 200 | 5-10s | 无 | 所有端点 |
| **L1.5 Service Integration** | 后端全流程: POST 创建→等待完成→GET 验证结果 | 10-60s | 无 | 每个核心业务链路 1 条 |
| **L2a Route/Model-DB** | 路由注册 + 模型列 = 数据库列 | <5s | 无 | 自动生成 |
| **L2b Mock Integration** | 核心 Service Pipeline, mock 外部依赖 | <5s | CLI/DB/HTTP | 每个 Service 1-2 条 |
| **L3 Component** | mount 组件 → 断言 DOM 内容 | <2s | 外部数据 | computed/render 逻辑 |
| **L4 Logic Unit** | 纯函数: 状态持久化, 数据变换 | <1s | 无 | 每个纯函数 2-3 条 |

**L1 Shell vs L1.5 Service 的区别**：
- L1 Shell: `curl /api/health` → 只验证端点存在, 返回 200 就行
- L1.5 Service: `POST /api/executions` → 等执行完成 → `GET /api/executions/{id}` → 断言 status=completed, step_results 非空, 产物已生成

**L1.5 Service vs L2b Mock 的区别**：
- L2b Mock: `with patch("stream_claude", mock_events)` → 快速验证代码逻辑
- L1.5 Service: 真实调用 Claude CLI → 验证完整链路（包括 subprocess 启动、文件写入、DB 写入）

## Language-Specific Tooling Matrix

| Layer | Python | Java (Spring Boot) | Go | Node.js |
|-------|--------|---------------------|-----|---------|
| **L0 E2E** | Playwright (npx) | Playwright (npx) | Playwright (npx) | Playwright / Cypress |
| **L1 Shell** | bash + curl | bash + curl | bash + curl | bash + curl |
| **L1.5 Integration** | pytest + httpx (真实调用) | @SpringBootTest + TestRestTemplate | testing + net/http | jest + supertest |
| L2a Route/DB | pytest + TestClient | JUnit 5 + MockMvc + @DataJpaTest | testing + httptest | jest + supertest |
| L2b Mock | pytest + unittest.mock | JUnit 5 + Mockito | testing + testify/mock | jest + nock |
| L3 Component | vitest + vue/test-utils | N/A (无前端) | N/A (无前端) | vitest + testing-library |
| L4 Logic | pytest | JUnit 5 | testing + testify | vitest

### Language Detection

Before generating tests, detect the project language from these signals:

| Signal | Language |
|--------|----------|
| `pom.xml`, `build.gradle`, `*.java` | **Java** |
| `go.mod`, `*.go`, `main.go` | **Go** |
| `pyproject.toml`, `requirements.txt`, `*.py` | **Python** |
| `package.json` + `*.tsx`/`*.jsx` | **Node.js/TypeScript** |

For mixed-stack projects (e.g., Python backend + Vue frontend), generate each layer in the appropriate language.

## 测试选择 API（🆕 v1.2 新增，D6 修复 - 单一事实源）

本 Skill 是测试选择算法的**唯一定义者**。其他 Skill（`deep-planning-rigorous-validation` 阶段7.0、`fix-bug-workflow` Step 3）**必须调用 `select_tests.py` 脚本**，不得内联 find/grep 逻辑，消除三套重复算法。

### 入口

```bash
python3 skills/test-pyramid-builder/scripts/select_tests.py \
    --changed-files <file1>,<file2> \
    --depth <L1|L2|L3|L4> \
    --json
```

也支持 stdin 管道（配合 `git diff --name-only`）：
```bash
git diff --name-only | python3 skills/test-pyramid-builder/scripts/select_tests.py --depth L3 --json
```

### depth 映射（与下方 Adaptive Test Selection 对齐）

| depth | 对应 P 级 | 耗时 | 检查层 |
|-------|----------|------|--------|
| L1 | P3 | ~5s | 文件名匹配 |
| L2 | P2 | ~10s | + 同目录 + 符号引用 |
| L3 | P1 | ~30s | + 金字塔层级（mock 天花板需手动调 test-pyramid-builder 完整检查，select_tests.py 不含） |
| L4 | P0 | ~60s | + 全影响面 + 依赖追踪 |

### 输出

```json
{
  "selected": ["control_plane/tests/test_release_gates.py"],
  "selected_count": 1,
  "depth_used": "L2",
  "layers_checked": ["L1-filename", "L2-symbol"],
  "changed_files": ["control_plane/release_gates.py"]
}
```

### 噪音过滤

自动排除：`__pycache__` / `.pyc` / `benchmark/snapshots/` / `benchmark/change-backups/` / `.claude/worktrees/` / `node_modules` 等副本与缓存。

### 下方「Adaptive Test Selection」章节的角色

下方章节是 select_tests.py 的**行为说明文档**（L1-L4 各层匹配规则的定义），供实现与审计参考。调用方应调用脚本而非复制其逻辑。

---

## Adaptive Test Selection — 按 Bug 严重程度自适应匹配

**核心原则**：轻量 Bug 快速过，严重 Bug 全面扫。不搞一刀切。

```
P3 轻微     →  L1 文件命名匹配          (5s)
P2 一般     →  L2 同目录扩展             (10s)
P1 普通     →  L3 金字塔层级检查         (30s)
P0 严重     →  L4 全影响面 + 依赖追踪    (60s)
```

### L1 — 文件命名匹配（P3 及以上）

最简单、最确定。改了哪个文件，就找对应的测试文件。

```bash
# 改了 execution_engine.py → 找 test_execution*.py
# 改了 ChatPanel.vue      → 找 *ChatPanel*.spec.ts
# 改了 knowledge.py       → 找 test_knowledge*.py

find . -name "test_*$(basename $CHANGED_FILE .py)*" \
    -o -name "*$(basename $CHANGED_FILE .vue)*.spec.*"
```

**匹配规则**（确定性，不做模糊判断）：
- 提取变更文件的模块名（去掉后缀、路径）
- 搜索 `test_*<module>*` 和 `*<module>*.spec.*` 模式
- **找到了** → 运行，验证 RED 阶段
- **没找到** → 升级到下一层

### L2 — 同目录 + 同模块扩展（P2 及以上）

L1 没找到时，扩大范围到整个目录和相关模块。

```bash
# 改了 backend/services/execution_engine.py
# L1: test_execution_engine*.py → 无匹配
# L2: backend/tests/ 下全部测试 + 涉及 Execution 模型的测试

find $(dirname $CHANGED_DIR)/tests -name "test_*.py"
grep -rl "Execution\|execution_engine" tests/ backend/tests/
```

**匹配规则**：
- 同目录下所有测试（`services/` → `tests/`）
- 引用变更文件中类名/函数名的测试（`grep -l "ClassName" tests/`）

### L3 — 金字塔层级检查（P1 及以上） + Mock 天花板检测（v1.1）

检查变更涉及的每一层是否至少有一个测试覆盖，**同时检测 mock 天花板**。

| 变更涉及 | 必须有的测试层 | 检查方式 |
|---------|-------------|------|
| API 路由改了 | L1 Shell 冒烟包含该路由？ | `grep $ROUTE tests/smoke_api.sh` |
| 模型加了列 | L2a expect_columns 包含该表？ | `grep $TABLE test_route_smoke.py` |
| Service 改了 | **L1.5 真实调用** + L2b mock pipeline | `grep -L "patch\|mock" $(grep -rl "$SERVICE" backend/tests/)` |
| 前端组件改了 | L3/L4 vitest 覆盖？ | `find src/__tests__ -name "*$COMPONENT*"` |

**v1.1 变更**: Service 层变更必须同时有 L1.5（真实调用）和 L2b（mock），不能只有 L2b。

**任何一层缺失** → 调用 `test-pyramid-builder` 为该层生成测试。

### 🔴 Mock 天花板检测（v1.1 新增）

**在 L3 检查完成后，必须计算 mock 占比。**

```bash
# 统计后端测试中 mock 的比例
total_tests=$(find backend/tests -name "test_*.py" -exec grep -l "def test_" {} \; | wc -l)
mock_tests=$(find backend/tests -name "test_*.py" -exec grep -l "patch\|mock\|MagicMock" {} \; | wc -l)
mock_pct=$((mock_tests * 100 / total_tests))
echo "Mock 占比: $mock_pct% ($mock_tests/$total_tests)"
```

**判定规则**：

| Mock 占比 | 状态 | 动作 |
|-----------|:----:|------|
| < 50% | ✅ 正常 | 无需额外动作 |
| 50%–80% | ⚠️ 警告 | 检查 L1.5 覆盖，至少 1 条/模块 |
| > 80% | 🔴 Mock 天花板 | **强制生成 L1.5 测试**，不可跳过 |

**Mock 天花板解释**：当 80%+ 的测试都 mock 了同一层（如 `stream_claude`），意味着：
- 所有测试验证的是"mock 正确"，不是"链路正确"
- 无法发现分叉路径不可达的问题
- 覆盖率数字无意义 —— 100% 通过 ≠ 业务流正常

**触发历史案例**: skill-manager-service v0.11.0 — 57/57 测试全绿，但知识库 Tools 在链匹配路径下完全不可用。根因是全部 57 个测试都 mock 了 `stream_claude`，无人验证真实链路。

### L4 — 全影响面分析 + 分叉路径覆盖（P0 专用, v1.1 增强）

P0 级别 Bug 必须追踪全部受影响的代码路径，**包括所有分叉路径**。

```bash
# 1. 依赖追踪：谁 import 了变更的模块？
grep -rn "from backend.services.$MODULE import\|import backend.services.$MODULE" backend/

# 2. API 端点依赖：哪些端点调用了变更的函数？
grep -rn "$FUNCTION_NAME" backend/api/

# 3. 数据流追踪：变更的模型被哪些 service 查询？
grep -rn "$MODEL_NAME" backend/services/ backend/api/

# 4. 🔴 分叉路径检测（v1.1 新增）：识别函数中的提前 return / raise
#    每个 return 都是一个分叉点，必须有测试覆盖
grep -n "return\|raise HTTPException" backend/services/<changed_service>.py | \
  while read line; do
    echo "分叉点: $line"
    echo "  → 检查: 该分叉路径是否有测试覆盖?"
  done

# 5. 运行受影响的全部测试
# 6. 运行全量冒烟测试 (L1+L2+L3+L4)
# 7. 必须通过 E2E Playwright 核心链路
```

### 🔴 分叉路径覆盖检查（v1.1 新增）

**任何包含提前 `return` 或 `raise` 的函数，必须对每条分叉路径有独立的测试用例。**

检测方法：

```bash
# 识别目标函数中的所有分叉点
awk '/^async def stream_chat_response/,/^async def|^def/' backend/services/chat_service.py | \
  grep -n "return\|raise"
```

**检查清单**：

| 分叉点 | 示例 | 测试要求 |
|--------|------|---------|
| 提前 return | `if has_trigger: return` | 验证 return 之后的代码在另一路径被执行 |
| 条件跳过 | `if not kb_ids: return ""` | 验证 kb_ids=None 和 kb_ids=[1] 两条路径 |
| 异常抛出 | `raise HTTPException(404)` | 验证异常被正确触发和处理 |

**典型遗漏模式**：

```python
# chat_service.py
if has_trigger:
    yield skill_trigger
    return  # ← 分叉点！之后的代码不会执行

# Phase 2: 仅 has_trigger=False 时执行
build_agent_tools_prompt(...)  # ← 这条路径必须有独立测试！
```

**触发历史案例**: skill-manager-service v0.11.0 — `has_trigger: return` 跳过了 Phase 2，导致知识库 Tools 在链匹配路径下不可用。所有 mock 测试全绿，因为测试只验证了 `stream_claude` 被 mock 后的行为，没有验证 `stream_claude_with_tools` 是否被调用。

### 决策流程（v1.1 更新）

```
Bug 分级判定
    ↓
┌───────────────────────────────────────────────────┐
│ Step 0 (v1.1): 业务流映射 — 所有等级都必须执行      │
│   扫描 project-note → 提取业务流 → 交叉匹配测试     │
│   有 ❌/⚠️ 标记的流 → L1.5 必选                    │
├───────────────────────────────────────────────────┤
│ P3: L1 文件命名匹配                               │
│     找到? → 运行 → 通过 → 完成                    │
│     没找到? → 跳过（P3 不强制有新测试）            │
├───────────────────────────────────────────────────┤
│ P2: L1 → L2 同目录扩展                            │
│     找到? → 运行 → 通过 → 完成                    │
│     没找到? → 生成 1 层测试（最相关的）            │
├───────────────────────────────────────────────────┤
│ P1: L1 → L2 → L3 金字塔检查 + Mock 天花板         │
│     每层都有覆盖? → 完成                          │
│     有缺失层? → 为该层生成测试                    │
│     🔴 Mock > 80%? → 强制生成 L1.5               │
├───────────────────────────────────────────────────┤
│ P0: L1 → L2 → L3 → L4 全影响面 + 分叉路径         │
│     依赖追踪 + 全量冒烟 + E2E 核心链路             │
│     🔴 每条分叉路径必须有独立测试                   │
│     🔴 Mock > 50%? → 生成 L1.5 覆盖每条业务流     │
│     所有受影响测试必须通过                         │
└───────────────────────────────────────────────────┘
```

**v1.1 新增规则**：
- Step 0 业务流映射为**所有等级必做**（新增）
- P1 及以上自动触发 Mock 天花板检测（新增）
- P0 必须检查分叉路径覆盖（新增）
- **任何时候测试全绿但 Mock > 80% → 自动触发 L1.5 生成，不可跳过**（新增）

**关键规则**：
- 每一步都是**确定性操作**（grep / find / 跑测试），不做模糊匹配
- 任何时候找到匹配测试 → 直接运行，**不继续扩大范围**（除非 P0）
- 生成新测试后 → 必须登记到 `test-pyramid-builder` 生成的测试文件中
- P3/P2 不强制生成新测试，P1/P0 必须覆盖到金字塔各层

## What Gets Generated — 按严重程度决定生成范围

Adaptive Selection 找到匹配测试 → 直接跑，不生成。**只有找不到匹配时才生成**，且生成范围由 Bug 严重等级决定。

```
                     P3          P2           P1           P0
                     ──          ──           ──           ──
找到匹配测试?    →  只跑不生成   只跑不生成    只跑不生成    只跑不生成
没找到?
  L1 Shell        →  跳过        跳过          生成 ✅       生成 ✅
  L2a Route/DB    →  跳过        跳过          生成 ✅       生成 ✅
  L2b Mock 集成   →  跳过        只生成最相关   生成 ✅       生成 ✅
  L3 组件单测     →  跳过        跳过          生成 ✅       生成 ✅
  L4 逻辑单测     →  跳过        跳过          生成 ✅       生成 ✅
  L0 E2E          →  跳过        跳过          跳过          生成 ✅
```

**举例**：

```
改了 backend/services/execution_engine.py 的 project.path 引用

P3: find test_execution*.py → 找到 test_execution_pipeline.py → 跑 → 完 (0 生成)
P2: 同上, 找到 → 跑 → 完 (0 生成)
P1: 同上, 找到 → 跑 → 但检查金字塔:
     L1 smoke 有没有 /api/executions? ✅
     L3 组件有没有覆盖? ❌ → 生成 ResponseCard 组件测试
     L4 逻辑有没有覆盖? ❌ → 生成 autoSelectExecution 逻辑测试
P0: 同上 + 全依赖追踪 → 跑所有受影响测试 + 生成 L0 E2E 核心链路
```

```
改了 frontend/src/components/ResponseCard.vue 的 thinkingContent

P3: find *ResponseCard*.spec.ts → 找到 → 跑 → 完 (0 生成)
P2: 同上 (0 生成)
P1: 找到 → 跑 → 但 L1 smoke 有没有 vercel 路由? → 不涉及后端, 跳过 L1/L2
     L3 ✅ L4 ✅ → 金字塔完整, 不生成
P0: 同上 + Playwright E2E: 验证聊天页面深度思考区域显示正常
```

**关键原则**：
- P3/P2 几乎不生成新测试，只跑已有的。这降低了"为小事写测试"的心理负担
- P1 补齐金字塔缺失层 — 已有测试不重复生成，只补缺口
- P0 全覆盖 — 不信任任何一层，全部重新验证
- **改了什么文件就生成什么语言的测试**（Python → pytest, Vue → vitest, Java → JUnit）

## Build Process — 具体生成步骤

### Step 0: Map Business Flows to Tests（v1.1 新增）

**必须在生成任何测试之前执行此步骤。** 这是防止"测试全绿但业务不通"的第一道防线。

**0.1 扫描业务流文档**

调用 `query-knowledge` 检索项目业务流知识库：

```bash
# 获取 KB 索引（确认业务流分类存在）
python3 skills/query-knowledge/scripts/query_knowledge.py \
    --repo . --backend project-note --json overview

# 检索业务流相关文档（替代手动 ls + read）
python3 skills/query-knowledge/scripts/query_knowledge.py \
    --repo . --backend project-note --json \
    search "业务流 数据流 派发 状态机 路由" --top 5 --context feature

# 也可按分类直接列出（如果 overview 显示有 03-核心数据业务流 分类）
python3 skills/query-knowledge/scripts/query_knowledge.py \
    --repo . --backend project-note --json \
    category "03-核心数据业务流"
```

如果 `overview` 返回 `kb_source: not-found`，或 search 命中 0 条业务流文档，记录"本项目无业务流知识库"，跳到 Step 1 按常规流程生成测试。

如果命中，提取每条业务流的数据流路径和关键节点（read top-1~2 全文即可）。

**0.2 输出业务流清单**

从文档中提取，格式：

```markdown
| # | 业务流 | 入口 | 关键节点 | 分叉点 | 测试覆盖 |
|---|--------|------|---------|--------|:---:|
| 1 | 聊天SSE流式响应 | GET /chat-stream | Phase 0→1→2→3 | has_trigger | ❌ |
| 2 | 知识库上传搜索 | POST /upload → GET /search | 提取→存储→索引→搜索 | - | ✅ |
```

**0.3 交叉匹配现有测试**

对每条业务流，检查：

```bash
# 入口 API 是否有 L1.5 集成测试？
grep -rl "chat-stream\|/search\|/upload" backend/tests/test_integration*.py

# 关键函数是否有非 mock 测试？
grep -rl "stream_chat_response" backend/tests/ | xargs grep -L "patch\|mock"
```

**0.4 标记未覆盖的业务流**

| 状态 | 条件 |
|------|------|
| ✅ 已覆盖 | 该业务流的入口 API 有 L1.5 测试 + 关键路径有非 mock 验证 |
| ⚠️ 部分覆盖 | L2b mock 测试存在，但无 L1.5 真实调用 |
| ❌ 未覆盖 | 该业务流的所有关键节点均无测试 |

**任何 ❌ 或 ⚠️ 标记的业务流 → L1.5 测试为必选项。**

### Step 1: Extract what to test from your bugfixes

For each bug you just fixed, ask: "What assertion would have caught this before I wrote any code?"

| Bug Symptom | Test Type | Assertion Pattern |
|------------|-----------|-------------------|
| API endpoint 404 | L2a Route smoke | `assert route in app.routes` |
| Column missing in DB | L2a Model/DB consistency | Compare `PRAGMA table_info` with model fields |
| Wrong import → silent failure | L2b Mock integration | `with patch(...)` + verify event pipeline |
| Component ignores message type | L3 Component | Mount with messages → `expect(text).toContain(...)` |
| State lost on refresh | L4 Logic | `persist(id)` → `expect(restore()).toBe(id)` |

### Step 2: Generate each layer from project structure

**L1 — Shell smoke script** (`tests/smoke_api.sh`):
```bash
# Extract all routes from the codebase
grep -rn "router\.\(get\|post\|put\|delete\|patch\)" backend/api/ | \
  while read line; do
    # Extract path → generate curl check
  done
```
Write a script that curls every endpoint. Start the project, run `bash tests/smoke_api.sh`, see ✅ or ❌ in 10 seconds.

**L2a — Route + Model-DB consistency test** (`backend/tests/test_route_smoke.py`):

Template:
```python
# 1. Route registration test
REQUIRED_ROUTES = ["/api/projects", "/api/sessions/{id}/messages", ...]
@pytest.mark.parametrize("route", REQUIRED_ROUTES)
def test_required_route_registered(app_routes, route):
    assert route in app_routes

# 2. Model column = Database column test
EXPECTED_COLUMNS = {"executions": ["id", "pre_execution_state", ...], ...}
@pytest.mark.parametrize("table,expected_cols", EXPECTED_COLUMNS.items())
async def test_model_columns_match_database(table, expected_cols):
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    actual = {row[1] for row in result}
    assert not [c for c in expected_cols if c not in actual]
```

**L2b — Mock integration test** (`backend/tests/test_execution_pipeline.py`):

Find the core service function. Mock external dependencies (CLI, HTTP, DB). Verify the event/result pipeline is complete.

```python
MOCK_CLAUDE_EVENTS = [{"type": "thinking_delta", ...}, ...]

async def test_pipeline_yields_all_events():
    with patch("module.stream_claude", mock_stream):
        events = [e async for e in stream_next_step(mock_db, 1)]
    assert "step_started" in types
    assert events[-1]["type"] == "step_completed"
```

**L3 — Component test** (`frontend/src/__tests__/Component.spec.ts`):

Find the component with the most `computed()` logic. Mount it with different prop combinations. Assert DOM content.

```typescript
describe('thinkingContent', () => {
  it('shows thinking type messages (was: bug)', () => {
    const wrapper = mount(Component, { props: { messages: [
      { message_type: 'thinking', content: 'Matched skill X', ... }
    ]}})
    expect(wrapper.text()).toContain('Matched skill X')
  })
})
```

**L4 — Logic test** (`frontend/src/__tests__/logic.spec.ts`):

Extract the pure function from the component. Test input → output. No mounting needed.

```typescript
describe('sessionStorage persistence', () => {
  it('restores after save', () => {
    persist(94)
    expect(restore()).toBe(94)
  })
})
```

### Step 3: Choose tools and write tests by language

Detect the project language (see Language Detection above), then use the templates below.

**Language-agnostic L1** (`tests/smoke_api.sh`) — same for all stacks:
```bash
#!/bin/bash
# Extract routes from the backend framework:
# Python: grep -rn "@router\.\(get\|post\)" backend/
# Java:   grep -rn "@\(GetMapping\|PostMapping\)" src/
# Go:     grep -rn "mux\.\(HandleFunc\|GET\|POST\)" .
# Then curl each one.
```

**Python (FastAPI/Django)** — L2a Route + Model-DB:
```python
# Route registration
REQUIRED_ROUTES = ["/api/projects", "/api/sessions/{id}/messages"]
@pytest.mark.parametrize("route", REQUIRED_ROUTES)
def test_required_route_registered(app_routes, route):
    assert route in app_routes

# Model-DB consistency (SQLAlchemy)
async def test_model_columns_match_database(table, expected_cols):
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    actual = {row[1] for row in result}
    assert not [c for c in expected_cols if c not in actual]
```

**Python** — L2b Mock Integration:
```python
from unittest.mock import patch, AsyncMock

async def test_pipeline_yields_all_events():
    with patch("module.external_dependency", mock_func):
        events = [e async for e in core_function(mock_db, id)]
    assert events[-1]["type"] == "step_completed"
```

**Java (Spring Boot)** — L2a Route + JPA-DB:
```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class RouteSmokeTest {
    @Autowired private TestRestTemplate restTemplate;

    @ParameterizedTest
    @ValueSource(strings = {"/api/projects", "/api/sessions/1/messages"})
    void allCoreRoutesReturn200(String path) {
        var resp = restTemplate.getForEntity(path, String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
    }
}

@DataJpaTest
class ModelColumnConsistencyTest {
    @Autowired private EntityManager em;

    @Test
    void executionTableHasAllExpectedColumns() {
        var columns = em.createNativeQuery("PRAGMA table_info(executions)")
            .getResultList();
        var colNames = columns.stream().map(r -> ((Object[])r)[1].toString())
            .collect(toSet());
        assertThat(colNames).contains("pre_execution_state", "pre_execution_snapshot");
    }
}
```

**Java** — L2b Mock Integration:
```java
@ExtendWith(MockitoExtension.class)
class ExecutionPipelineTest {
    @Mock private ClaudeAdapter claudeAdapter;
    @InjectMocks private ExecutionEngine engine;

    @Test
    void shouldEmitAllEventTypes() {
        when(claudeAdapter.stream(any()))
            .thenReturn(Flux.just(
                new ClaudeEvent("thinking_delta", "Analyzing..."),
                new ClaudeEvent("text_delta", "Done.")
            ));

        var events = engine.streamNextStep(executionId).collectList().block();

        assertThat(events).extracting("type")
            .contains("step_started", "claude_event", "step_completed");
    }
}
```

**Java** — L4 Logic Unit:
```java
class SessionStorageManagerTest {
    @Test
    void shouldPersistAndRestoreExecutionId() {
        var manager = new SessionStorageManager();

        manager.persist(94);
        assertThat(manager.restore()).isEqualTo(94);

        manager.persist(null);
        assertThat(manager.restore()).isNull();
    }
}
```

**Go** — L2a Route + DB:
```go
func TestAllRoutesRegistered(t *testing.T) {
    router := setupRouter()
    requiredRoutes := []string{
        "/api/projects",
        "/api/sessions/{id}/messages",
    }
    for _, route := range requiredRoutes {
        t.Run(route, func(t *testing.T) {
            req := httptest.NewRequest("GET", strings.Replace(route, "{id}", "1", 1), nil)
            w := httptest.NewRecorder()
            router.ServeHTTP(w, req)
            if w.Code == 404 {
                t.Errorf("route %s returned 404 — not registered?", route)
            }
        })
    }
}

func TestModelColumnsMatchDatabase(t *testing.T) {
    db := testDB(t)
    rows, _ := db.Query("PRAGMA table_info(executions)")
    cols := make(map[string]bool)
    for rows.Next() {
        var cid int; var name, typ string
        var notNull, pk int; var dflt sql.NullString
        rows.Scan(&cid, &name, &typ, &notNull, &dflt, &pk)
        cols[name] = true
    }
    required := []string{"id", "pre_execution_state", "pre_execution_snapshot"}
    for _, c := range required {
        if !cols[c] { t.Errorf("column %s missing from executions table", c) }
    }
}
```

**Go** — L2b Mock Integration:
```go
func TestPipelineYieldsAllEvents(t *testing.T) {
    mockCli := new(MockClaudeClient)
    mockCli.On("Stream", mock.Anything).Return(mockEvents, nil)

    engine := NewEngine(mockCli)
    events := collectEvents(engine.StreamNextStep(ctx, 1))

    types := extractTypes(events)
    assert.Contains(t, types, "step_started")
    assert.Contains(t, types, "claude_event")
    assert.Equal(t, "step_completed", events[len(events)-1].Type)
}
```

**Go** — L4 Logic Unit:
```go
func TestPersistRestoreExecutionId(t *testing.T) {
    store := NewExecStore()

    store.Persist(94)
    got, ok := store.Restore()
    assert.True(t, ok)
    assert.Equal(t, 94, got)

    store.Persist(0) // clear
    _, ok = store.Restore()
    assert.False(t, ok)
}
```

**Language-agnostic L1.5 — Service Integration test** (real API, no mocks):
```
This is THE most important layer for catching regressions.
Unlike L1 (just checks 200), L1.5 tests the COMPLETE business flow.
```

**Python** — L1.5 Service Integration (`backend/tests/test_integration.py`):
```python
import httpx, time, pytest

BASE = "http://localhost:8000/api"

@pytest.mark.integration  # mark so it can be skipped in CI without services running
def test_full_execution_flow():
    """端到端: 创建执行 → 等待完成 → 验证结果。依赖真实后端运行。"""
    # 1. 发起执行
    resp = httpx.post(f"{BASE}/executions/chains/1/execute",
        json={"mode": "auto", "user_query": "integration test"})
    assert resp.status_code == 200
    exec_id = resp.json()["id"]

    # 2. 轮询等待完成 (最多等 120s)
    for _ in range(120):
        resp = httpx.get(f"{BASE}/executions/{exec_id}")
        status = resp.json()["status"]
        if status in ("completed", "failed"): break
        time.sleep(1)

    # 3. 断言结果
    data = resp.json()
    assert data["status"] in ("completed", "failed"), f"stuck at {data['status']}"
    assert len(data["step_results"]) > 0, "no step results"
    # 4. 验证产物
    artifacts = httpx.get(f"{BASE}/executions/{exec_id}/artifacts").json()
    assert len(artifacts) > 0, "no artifacts generated"
```

**Java** — L1.5 Service Integration:
```java
@SpringBootTest(webEnvironment = RANDOM_PORT)
class ExecutionIntegrationTest {
    @Autowired TestRestTemplate rest;

    @Test
    void fullExecutionFlow() throws Exception {
        // 1. Start execution
        var start = rest.postForEntity("/api/executions/chains/1/execute",
            Map.of("mode", "auto", "user_query", "test"), Map.class);
        assertThat(start.getStatusCode()).isEqualTo(HttpStatus.OK);
        int execId = (Integer) start.getBody().get("id");

        // 2. Poll until done
        Map<String, Object> data = Map.of();
        for (int i = 0; i < 120; i++) {
            data = rest.getForObject("/api/executions/" + execId, Map.class);
            if (List.of("completed","failed").contains(data.get("status"))) break;
            Thread.sleep(1000);
        }

        // 3. Assert
        assertThat(data.get("status")).isIn("completed", "failed");
        assertThat((List<?>) data.get("stepResults")).isNotEmpty();
    }
}
```

**Go** — L1.5 Service Integration:
```go
func TestFullExecutionFlow(t *testing.T) {
    if testing.Short() { t.Skip("integration test requires running server") }

    // 1. Start execution
    resp, _ := http.Post("http://localhost:8080/api/executions/chains/1/execute",
        "application/json", strings.NewReader(`{"mode":"auto","user_query":"test"}`))
    assert.Equal(t, 200, resp.StatusCode)
    var start struct{ ID int `json:"id"` }
    json.NewDecoder(resp.Body).Decode(&start)
    resp.Body.Close()

    // 2. Poll until done
    var status string
    for i := 0; i < 120; i++ {
        resp, _ = http.Get(fmt.Sprintf("http://localhost:8080/api/executions/%d", start.ID))
        var exec struct{ Status string `json:"status"` }
        json.NewDecoder(resp.Body).Decode(&exec)
        resp.Body.Close()
        if exec.Status == "completed" || exec.Status == "failed" {
            status = exec.Status; break
        }
        time.Sleep(time.Second)
    }
    assert.Contains(t, []string{"completed","failed"}, status)
}
```

---

**Language-agnostic L0 — E2E Browser test** (real browser, front+back full chain):

L0 uses Playwright to drive a REAL browser. This is the only layer that catches CSS breaks, JS runtime errors, and frontend-backend integration issues.

```bash
# Install Playwright (once per project)
npm install -D @playwright/test && npx playwright install chromium
```

**L0 template** (`e2e/core-flow.spec.ts`) — same for ALL backend languages:
```typescript
import { test, expect } from '@playwright/test'

test.describe('Core user journey', () => {

  test('login → send query → see response', async ({ page }) => {
    // 1. Login
    await page.goto('http://localhost:5173/login')
    await page.fill('input[placeholder*="用户名"]', 'admin')
    await page.fill('input[placeholder*="密码"]', 'admin')
    await page.click('button:has-text("登 录")')
    await expect(page).toHaveURL(/\/$/ || /\/project/)

    // 2. Navigate to project
    await page.click('h3:has-text("test")')
    await page.waitForURL(/\/project\/\d+\/execute/)

    // 3. Send a query
    const input = page.locator('textarea[placeholder*="描述"]')
    await input.fill('say hello')
    await input.press('Control+Enter')

    // 4. Wait for response (summary section appears)
    await expect(page.locator('.summary-content')).toContainText('hello', { timeout: 60000 })

    // 5. Verify no console errors
    page.on('console', msg => {
      if (msg.type() === 'error') throw new Error(`Console error: ${msg.text()}`)
    })
  })

  test('manual chain selection → execution detail visible', async ({ page }) => {
    // ... project-specific core flow
  })
})
```

**L0 Playwright config** (`playwright.config.ts`):
```typescript
import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e',
  timeout: 120000,        // 2 min per test (execution takes time)
  expect: { timeout: 60000 },
  use: { baseURL: 'http://localhost:5173', headless: true },
  webServer: [  // auto-start services
    { command: 'python -m uvicorn backend.main:app --port 8000', port: 8000 },
    { command: 'npm run dev', port: 5173 },
  ],
})
```

### Step 4: Check test tooling and install if needed

```bash
# Check if vitest/jest is configured
ls vitest.config.* jest.config.* 2>/dev/null || echo "NEEDS SETUP"

# If no vitest config, create one:
cat > vitest.config.ts << 'EOF'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
export default defineConfig({
  plugins: [vue()],
  test: { environment: 'jsdom', globals: true,
    include: ['src/__tests__/**/*.spec.ts'] }
})
EOF

# Install dependencies
npm install -D vitest @vue/test-utils jsdom  # Vue
npm install -D vitest @testing-library/react jsdom  # React
```

### Step 4: Install test tooling per language

```bash
# Python (if pytest not configured)
pip install pytest pytest-asyncio httpx

# Java (if JUnit/Mockito not in pom.xml)
# Add to pom.xml:
#   <dependency>org.springframework.boot:spring-boot-starter-test</dependency>
#   <dependency>org.mockito:mockito-junit-jupiter</dependency>

# Go (standard library, no install needed)
# Tools: testing, net/http/httptest, testify (optional: go get github.com/stretchr/testify)

# Node.js frontend (if vitest not configured)
npm install -D vitest @vue/test-utils jsdom    # Vue
npm install -D vitest @testing-library/react jsdom  # React
```

### Step 5: Run everything, verify zero failures

```bash
# Python stack
bash tests/smoke_api.sh && uv run pytest backend/tests/ -v && npx vitest run

# Java stack
bash tests/smoke_api.sh && mvn test

# Go stack
bash tests/smoke_api.sh && go test ./...
```

### Step 6: 逆向验证（v1.3 新增，防"测试糊弄"+ AI 声明完成但业务不通）

**核心原则**：测试全绿 ≠ 测试有效。本步骤通过**故意破坏代码**验证测试能否发现，证明测试不是糊弄。

**为什么需要**：
- 2026-07-24 真实案例：v6.8.0 知识库 E2E 写了 `expect(merged.merged_doc_ids).toContain(doc.id)`，断言写错（service 返回新 doc.id 不是 source 的 doc.id），但测试一开始"全绿"因为没人运行
- AI 声明"测试已写"但断言逻辑错；deep-planning 声明"§3.6 已落地"但 UI 没改
- **根因**：没人做"逆向验证"--如果故意把代码改坏，测试会不会红？

**逆向验证 3 种模式**：

#### 模式 A：Mutation Testing（P0/P1 必做）

对每个新建的测试，**故意破坏被测代码的一个关键点**，验证测试会红：

```bash
# Python: 用 mutmut 或手工 mutation
# 1. 选一个测试覆盖的关键代码点
# 2. 故意改坏（如把 == 改成 !=，把 return True 改成 return False）
# 3. 跑测试 -> 必须红
# 4. 改回 -> 必须绿

# 示例：v6.8.0 knowledge service merge()
# 原代码：merged_doc_ids.append(int(result.lastrowid))
# Mutation：改成 merged_doc_ids.append(999)  # 假 ID
# 预期：test_merge_idempotent_rejected + test_archive_blocked_by_active_merges 应该红
# 如果没红 -> 测试糊弄，断言没真正检查 merged_doc_ids 内容
```

**Mutation 清单**（P0/P1 必跑）：

| 被测代码点 | Mutation | 预期红的测试 |
|-----------|----------|------------|
| `return True` | `return False` | 走该分支的测试 |
| `==` 比较 | `!=` | 等值断言的测试 |
| `raise KB_REVIEW_INVALID_STATE` | 注释掉 | 状态机非法转换测试 |
| `INSERT INTO ... VALUES` | 改表名 | DB 写入测试 |
| `if has_trigger: return` | 删除该行 | 分叉路径测试 |

**验证命令**：
```bash
# 手工 mutation（最简）
python3 skills/test-pyramid-builder/scripts/mutation_check.py \
    --target control_plane/knowledge/service.py \
    --tests control_plane/tests/test_knowledge/test_service.py \
    --mutations 5

# 输出：
# [PASS] mutation 1/5: 改 lastrowid -> 999, 测试红了 ✅
# [FAIL] mutation 2/5: 删除 raise KB_REVIEW_INVALID_STATE, 测试没红 ❌ -> 测试糊弄
```

#### 模式 B：契约破坏验证（P0 必做）

对每个声明"按契约 §x.y 落地"的变更，**故意删除一个契约点**，验证 E2E 会红：

```bash
# 示例：v6.8.0 §3.6 要求"关联知识库"按钮
# 1. 故意删除 KnowledgeGuideModal 渲染（注释掉）
# 2. 跑 tests/e2e/v680-knowledge-guide-contract.spec.js
# 3. 预期：Gate 11 行为门禁测试必须红（dialog 不出现）
# 4. 如果没红 -> E2E 没真正验证行为，只验证了"代码存在"

# 这就是为什么 Gate 11 要断言 toHaveCount(0) + getByRole('dialog')
# - toHaveCount(0) 验证旧 UI 已删除（删 dropzone）
# - getByRole('dialog') 验证新 UI 已渲染（弹窗出现）
# 二者结合 = 行为门禁，不是代码存在门禁
```

#### 模式 C：Mock 天花板逆向（v1.1 补强）

当 Mock 占比 > 50% 时，**故意把 mock 返回值改错**，验证测试会红：

```bash
# 示例：mock stream_claude 返回 events
# 1. 故意把 mock 返回的 events 删掉最后一个 step_completed
# 2. 跑 L2b mock 测试 -> 必须红
# 3. 如果没红 -> mock 测试只验证"mock 被调用"，没验证"事件序列完整"

# v1.1 的 Mock 天花板检测 + v1.3 的逆向验证 = 双保险
```

**逆向验证决策矩阵**：

| 变更等级 | 模式 A Mutation | 模式 B 契约破坏 | 模式 C Mock 逆向 |
|---------|:--------------:|:--------------:|:---------------:|
| P0 严重 | ✅ 必做（≥5 个 mutation） | ✅ 必做 | ✅ 必做（如 Mock>50%） |
| P1 普通 | ✅ 必做（≥3 个 mutation） | ⚠️ 建议做 | ⚠️ 建议做 |
| P2 一般 | ⚠️ 建议做 | ❌ 跳过 | ❌ 跳过 |
| P3 轻微 | ❌ 跳过 | ❌ 跳过 | ❌ 跳过 |

**逆向验证报告模板**（P0/P1 必产出）：

```markdown
## 逆向验证报告（v1.3 Step 6）

| # | 被测代码点 | Mutation | 预期红的测试 | 实际结果 |
|---|-----------|----------|------------|:-------:|
| 1 | service.py:1005 merged_doc_ids.append | 改成 append(999) | test_merge_idempotent_rejected | ✅ 红 |
| 2 | review_fsm.py 非法转换 raise | 注释掉 | test_fsm_invalid_transition | ✅ 红 |
| 3 | routes.py /knowledge-bases 注册 | 注释掉 | test_openapi_registers_knowledge_endpoints | ✅ 红 |
| 4 | ProjectWorkspacePage dropzone 删除 | 恢复 dropzone | v680-knowledge-guide-contract | ✅ 红（Gate 11 toHaveCount(0) 失败） |
| 5 | KnowledgeGuideModal 渲染 | 注释掉 | v680-knowledge-guide-contract | ✅ 红（dialog 不出现） |

**结论**：5/5 mutation 被测试发现，测试有效，非糊弄。
```

**关键规则**：
- 任何 mutation 没被测试发现 -> **测试无效，必须补强**
- P0 变更未做逆向验证 -> **不得声明完成**
- 逆向验证报告必须随 PR 提交（deep-planning 阶段 8d 新增要求）

---

## Quick Reference: Which Test for Which Bug

| Bug Category | Layer | Approach |
|-------------|:-----:|----------|
| CSS breaks, JS runtime errors, front-back disconnect | **L0 E2E** | Playwright real browser |
| Missing route / wrong path | L1 Shell | Add route to smoke script |
| Business flow broken (multi-step API) | **L1.5 Integration** | Real API call + poll + assert result |
| Route file not imported | L2a | Route registration parametrized test |
| Model column ≠ DB column | L2a | PRAGMA vs model fields comparison |
| Service pipeline broken | L2b | Mock externals, assert event sequence |
| Component ignores data type | L3 | Mount + assert DOM content |
| State lost on navigation/refresh | L4 | Extract + test persistence logic |

## Common Mistakes

- **Writing E2E tests for everything** — E2E is slow and flaky. Only 1-2 core user journeys need it.
- **Testing implementation details** — Test behavior (rendered DOM, returned data) not internal state.
- **Skipping L1 because "we have API tests"** — Shell smoke catches configuration issues (wrong port, server not started) that pytest misses.
- **One massive test file** — Split by layer and concern. Each file tests one thing.
- **🔴 Trusting 100% green mock tests（v1.1 新增）** — 当 >80% 的测试 mock 了关键依赖时，覆盖率数字不可信。始终检查 Mock 天花板比例，超过阈值必须补 L1.5。
- **🔴 Ignoring fork paths（v1.1 新增）** — 任何 `if/return` 分支必须在两条路径上都有测试。一条路径的测试覆盖 ≠ 所有路径的测试覆盖。
- **🔴 Skipping Step 0 business flow mapping（v1.1 新增）** — 不先看 project-note 的业务流文档就开始写测试，会漏掉最关键的端到端链路。

## Real-World Impact

From the skill-manager-service project:

- **v1.0 交付**: Before: 3 test files, 18 tests, 0 frontend tests. Bugfixes verified manually. After: 7 test files, 79 tests across 4 layers. Full pyramid runs in <20 seconds. 6 bugs fixed in one session → all 6 now have automated regression protection.

- **v1.1 教训**: 79 tests 全绿，但 P0 Bug（知识库 Tools 未注入 Claude）仍然漏过。根因：79 tests 中 90% mock 了 `stream_claude`，无人验证分叉路径（`has_trigger: return`）。Mock 天花板 + 分叉路径遗漏 = "覆盖率 100% 但业务不通"。v1.1 新增的业务流映射、Mock 天花板检测、分叉路径覆盖检查就是为了防止此类问题。
