---
name: feat-design-planning-v3
version: 3.9
description: >-
  v3.9 - 🔒 契约格式强制：API 端点必须 `### METHOD /path`（heading 紧跟 METHOD、无编号/中文前缀/反引号）+ 请求/响应 fence JSON 示例块（contract-oracle-extractor 可机械解析），Phase 2 末跑 extractor 自证 coverage.ratio ≥ 0.80 方可进 Phase 3（堵历史版本 O-CONTRACT-GAP——契约“内容完整但机器不可解析”）。v3.8 - 🆕 Phase 4 任务格式强制规范：每个任务必须使用 `- [ ]` checkbox 格式（涉及文件/需求覆盖/验证点 三字段必填），作为下游 deep-planning/team-rigorous-delivery 的执行看板与追踪基线。v3.7 - 🆕 Phase 5.4 执行路由决策：基于 L1/L2/L3 变更等级输出路由建议。L1/L2 → deep-planning-rigorous-validation，L3 → team-rigorous-delivery-v3（多 Agent 并行，处理能力 ~3 倍加速）。v3.6 - 🆕 grill-me 集成。v3.5 adds L1/L2/L3-gated adversarial review。
evaluated_by:
  - SC-A-003
  - SC-E-001
  - SC-H-001
  - SC-H-002
  - SC-M-005
---

# Feat Design & Planning — 功能设计规划

## Overview

从需求到设计+规划文档的结构化流程。核心原则：**思考 → 简洁 → 精准 → 目标驱动**（遵循 Karpathy 准则）。每阶段硬性验证门禁，不搞多余迭代。

- **v3.9 核心变更**：契约格式强制——API 端点必须 `### METHOD /path`（heading 紧跟 METHOD、无编号/中文前缀/反引号）+ 请求/响应 fence JSON 示例块；Phase 2 末跑 `contract-oracle-extractor` 自证 `coverage.ratio ≥ 0.80` 方可进 Phase 3。堵历史版本 O-CONTRACT-GAP（2026-08-12 补跑实证：v6.31.0 文字列表写请求参数 → ratio 0.0 → FAIL；补 JSON 块 → ratio 1.0 → PASS）。

**双模式自动切换**：有需求文档 → 模式 A（基于文档分析）；自然语言描述 → 模式 B（交互澄清）。

## When to Use

- 用户描述新功能：`我要做XXX` `新增XXX` `实现XXX`
- 用户提供需求文档路径（如 `docs/v2.1.0/需求文档.md`）
- 任何需要在写代码前完成设计的场景

**NOT for:** Bug 修复（→ `fix-bug-workflow`）、纯重构、性能优化（→ `performance-optimization`）

## 🛑 硬性规范：图 + 图解（Diagrams + Explanations — MANDATORY）

**Why this rule exists**：图是"骨架"，**没有文字解读的图无法作为后续迭代开发的规则**。每次评审 / 实施 / code review 都需要"图 + 图解"才能正确理解设计意图。本规范基于 v0.6.2 实施时的真实反馈固化。

### 规则 1：流程图需要添加（Diagrams are MANDATORY）

| 阶段 | 必须配图的内容 | 推荐图类型 |
|------|--------------|----------|
| Phase 3 设计文档 | 架构 / 数据流 / 状态 / 时序 | `classDiagram` / `flowchart` / `sequenceDiagram` / `stateDiagram-v2` |
| Phase 4 规划文档 | 任务依赖关系 | `graph TD` (DAG) |
| Phase 5 评审报告 | 测试金字塔 / 评分维度 | `graph TB` (层级图) |

- ✅ 设计文档**至少**包含 1 张 Mermaid 图
- ✅ 涉及**架构**、**数据流**、**状态**、**时序**、**依赖** 时**必须**配图
- ❌ 禁止"全文字"设计文档（除非纯概念描述）

### 规则 2：每张图必须配详细图解（Explanation is MANDATORY）

每张 Mermaid 图**紧邻的下方**必须有 `📖 **图解说明**` 块，**5 个固定板块**（缺一不可）：

```
📖 图解说明
├── 本图表示        what does this diagram show
├── 节点含义        what each node/element means
├── 关系含义        what each edge/arrow means
├── 实施规则        concrete rules for implementation
└── 边界情况        edge cases and how to handle them
```

**为什么"实施规则"和"边界情况"是必填的**：
- "节点含义"和"关系含义"是**读图**层面（看图说话）
- **"实施规则"** 才是**按图施工**的依据（程序员照着写代码）
- **"边界情况"** 是设计成熟度的试金石（说明设计者**真的想过**失效模式）

### 模板示例

```markdown
\`\`\`mermaid
flowchart TD
    A[入口] --> B{判断}
    B -->|yes| C[处理]
    B -->|no| D[返回]
\`\`\`

> 📖 **图解说明**
>
> **本图表示**：本流程图说明 X 模块的判断逻辑。
>
> **节点含义**：
> - `A[入口]`：调用入口
> - `B{判断}`：条件判断节点
> - `C[处理]` / `D[返回]`：处理路径
>
> **关系含义**：
> - `A → B`：调用必走
> - `B -->\|yes\| C`：条件 yes 走处理
>
> **实施规则**：
> 1. **判断逻辑必须短路**——先判断 cheap path，再判断 expensive path
> 2. **错误处理必须在 B 之后立即**——不能让异常传播到 C
>
> **边界情况**：
> - 传入参数为 None → 走 D 路径
> - C 内部异常 → 必须 catch 并降级到 D
```

### SDD 阶段例外

Phase 2 SDD 是**纯契约定义**（API/事件/类型），通常**不强制配图**——契约本身就是结构化的。但如果有复杂状态机或事件流，**推荐**配 `sequenceDiagram`。

---

## Process

### Phase 0: Context Loading (auto, no user input)

```
query-knowledge overview → 获取 KB 索引 + 每分类 L2 摘要（~500 tokens）
+ 读取 CLAUDE.md → 提取约束清单
```

**渐进式披露原则**：不一次性加载所有 L3 文档（避免 ~12k tokens 灌入上下文）。

1. **获取 KB 索引**（替代全量读）：
   ```bash
   python3 skills/query-knowledge/scripts/query_knowledge.py \
       --repo . --backend project-note --json overview
   ```
   返回 `{kb_root, kb_source, categories:[{category, l2_summary, l3_titles}]}`。
   仅当 `kb_source` 为 `not-found` 时才需要警告用户建 KB。

2. 读取项目根 `CLAUDE.md`（项目级硬约束）

3. **建立约束清单**（基于 L2 摘要 + CLAUDE.md，不需要读 L3 全文）：
   - 架构约束（从 01-架构总览 L2 摘要提取）
   - 现有设计模式（从 02-功能结构和策略 L2 摘要提取）
   - 不可违背规范（从 05-项目开发规范 L2 摘要提取）

4. **L3 文档按需加载**（Phase 1/3 用 search 触发，不在此阶段全量读）

**🚪 Gate:** `overview` 返回 `total_docs >= 3` 且 CLAUDE.md 存在，否则终止并报告缺失。
**🚪 Fallback:** 若 query-knowledge 脚本不存在或 KB 不可用，降级为旧方案（直接 read 全部 `docs/project-note/*.md`）。

### 🔹 Phase 0.5: 需求对抗审查 — 🆕 grill-me（手动模式，不中断流程）

grill-me（mattpocock/skills）适合需求不明确时做深度追问。**因需要用户交互，默认不自动调用**，仅在以下场景建议手动触发：

```markdown
# 当需求边界模糊 / 有多个可行方向时，在 Phase 1 前手动：
/grill-me "需求: <brief>"
# grill-me 会逐层追问直到所有问题闭环，然后继续
```

**自动流程不受影响**：未手动触发则跳过此步，直接进入 Phase 1。

### 🔹 Phase 5.3: 评审后对抗审查 — 🆕 grill-me（手动模式）

评分通过后，如需额外安全审查，手动调用（不中断自动流程）：

```markdown
/grill-me "评审结论: <自评摘要>"
```

未安装则跳过。

### Phase 1: Requirement Clarification

**模式 A — 有需求文档：**
1. 读取需求文档，提取核心关键词（功能名 / 涉及模块 / 技术栈）
2. **按需检索 L3 文档**（替代 Phase 0 全量读）：
   ```bash
   python3 skills/query-knowledge/scripts/query_knowledge.py \
       --repo . --backend project-note --json \
       search "<需求关键词>" --top 5 --context feature
   ```
   返回 top-5 相关 L3 文档（含 score / matched_sections / snippet）。
   仅对 top-1 或 top-2 调 `read` 加载全文，其余看 snippet 判断。
3. 与约束清单交叉验证，标记冲突点（如需求违背三级兜底策略）
4. 头脑风暴聚焦「如何在现有架构内最优实现」
5. 提出 2-3 个可行方向 + 推荐方案
6. 输出：需求边界 + 约束冲突 + 选定方向

**模式 B — 自然语言描述：**
1. 一次只问一个问题（聚焦目的/约束/成功标准）
2. 每个回答后更新理解
3. 需求边界清晰后，**按需求关键词检索 L3**（同模式 A 步骤 2）
4. 提出 2-3 个可行方向 + 推荐方案
5. 输出：需求边界 + 成功标准 + 选定方向

**🚪 Gate:**
- 需求边界不含模糊词（"大概""可能""等等"）
- 成功标准可验证（定量指标，非主观描述）
- 至少明确否决了 1 个替代方案（证明经过思考）

### Phase 2: SDD — 前后端契约定义

**🛑 Hard Gate：** 本阶段不可跳过。凡涉及前后端协作的功能，必须先定义契约再进入设计。纯后端/纯前端功能可跳过，但必须在 Phase 3 设计文档中注明「无跨端协作，跳过 SDD」，且下游业务质量门禁（deep-planning 阶段8f / fix-bug Phase E）须用 `business-quality-gate.py --no-sdd` 显式声明并仍提供真实验证器——跳过 SDD 只免除契约生成，不免除 KB 业务流验证与真实业务验证。

#### 2.1 判定是否需要 SDD

| 判断条件 | 是否需要 SDD |
|----------|-------------|
| 涉及新增 API 端点或修改现有 API | ✅ 需要 |
| 涉及 SSE/WebSocket 事件新增或变更 | ✅ 需要 |
| 涉及数据库表结构变更且前端消费 | ✅ 需要 |
| 涉及前后端数据格式变更 | ✅ 需要 |
| 纯后端计算/任务调度（无前端消费） | ❌ 可跳过 |
| 纯前端 UI 调整（API 不变） | ❌ 可跳过 |

#### 2.2 契约文档生成

写入 `docs/vX.Y.Z/契约规范.md`，包含以下章节：

**🔒 格式强制（v3.9）——契约必须可被质检机械解析：**

`docs/vX.Y.Z/契约规范.md` 的 API 端点将被 `contract-oracle-extractor` 机械提取生成 oracle（供下游 `ai-quality check --profile code-verified` 落锤）。**契约写作即语法，以下格式是唯一合法写法**：

| 强制项 | 合法写法 | 反例（不合格） |
|--------|---------|---------------|
| 端点 heading | `### POST /api/v1/xxx`（heading 后**直接**跟 METHOD，无编号 / 无中文前缀 / 无反引号） | `### 1.1 新增端点 POST /xxx`、`### 1.1 GET /xxx`、`### 新增 GET /xxx` |
| 请求参数 | **fence 内 JSON 示例块**（marker：`请求体` / `请求参数` / `请求示例`） | 仅文字列表 `- time_range（可选）：...`（extractor 推断不出 schema → gap） |
| 响应 | **fence 内 JSON 示例块**（marker：`成功响应` / `响应体` / `响应示例`） | 仅文字字段说明 |
| 错误码 | 状态码表格（`| 400 | INVALID_XXX | ... |`） | 仅文字描述 |

> **Why**：2026-08-12 历史补跑实证——v6.31.0 契约用文字列表写请求参数 → extractor ratio 0.0 → code-verified FAIL；补上 JSON 块后 ratio 1.0 → PASS。v6.38.0 严格按 `### GET /path` + JSON 块 → ratio 1.0 达标。**格式即覆盖**，heading 夹编号 / 文字列表 / 无 JSON 块 = 机器不可解析 = 契约不合格。

**1. API 契约**

为每个新增/修改的 API 端点定义：

```markdown
### POST /api/v1/xxx
- **用途**：[一句话描述]
- **权限**：[认证要求]
- **请求体**：
  ```json
  { "field": "type // 说明" }
  ```
- **成功响应** (200)：
  ```json
  { "data": { ... } }
  ```
- **错误响应**：
  | 状态码 | 错误码 | 说明 |
  |--------|--------|------|
  | 400    | INVALID_XXX | ... |
  | 404    | NOT_FOUND | ... |
- **SSE 事件流**（如适用）：
  | 事件名 | 触发时机 | payload 结构 |
  |--------|---------|-------------|
```

**2. 数据模型契约**

- **共享类型定义**（TypeScript/Python 双重定义，确保前后端类型一致）
- **数据库表变更**（新表 DDL / 修改表 ALTER / 索引定义）
- **枚举值契约**（前后端共用枚举的完整定义）

**3. 通信协议契约**

- SSE 事件目录（所有事件名 + payload 类型）
- WebSocket 消息类型（如有）
- 轮询/回调约定（如有）

**4. 错误处理契约**

- 本功能新增错误码清单
- 错误传播路径（后端 → API → 前端展示）
- 降级策略（必须符合 CLAUDE.md 三级降级体系）

**5. 前后端责任边界**

- 数据校验责任归属（哪些字段后端校验、哪些前端校验）
- 业务逻辑归属（计算在前端还是后端）
- 状态管理归属（哪些状态由前端管理、哪些由后端管理）

**🔒 契约可解析自证（v3.9 强制，写入契约后、进 2.3 前）：**

跑一次 `contract-oracle-extractor` 验证契约可被机械解析，`coverage.ratio ≥ 0.80`（或 gap 端点已显式标注排除理由）：

```bash
python3 skills/contract-oracle-extractor/scripts/extract.py \
    --sdd docs/vX.Y.Z/契约规范.md --output-dir tests/contract --project-root .
```

**通过判据**：`endpoints > 0` 且 `coverage.ratio ≥ 0.80`；任一端点 gap（`no_request_schema_defined` / `no_response_schema_defined`）→ 补 JSON 示例块后重跑，**不得带着 gap 进入 Phase 3**。

#### 2.3 SDD 自检清单

**🚪 Gate（全部通过方可进入 Phase 3）：**

- [ ] **（v3.9）契约可被 contract-oracle-extractor 机械解析**（heading 为 `### METHOD /path` + 请求/响应 fence JSON 块）
- [ ] **（v3.9）extractor 自证通过**（`endpoints > 0` 且 `coverage.ratio ≥ 0.80`，gap 端点已显式标注排除理由）
- [ ] 每个 API 端点都有完整的请求/响应/错误码定义
- [ ] 所有 SSE 事件都有事件名 + payload 类型
- [ ] 共享类型同时提供 TypeScript 和 Python 定义
- [ ] 错误码不重复、不与现有错误码冲突
- [ ] 降级策略与 CLAUDE.md 三级降级体系一致
- [ ] 前后端责任边界无歧义（不存在"都可以"的字段）
- [ ] 契约文档无 TODO/TBD 占位符

**🚪 Hard Gate：** 契约文档写入 `docs/vX.Y.Z/契约规范.md` 并全部 checklist 打勾后方可进入 Phase 3。

### Phase 3: Design Doc Generation

**设计前必做：错题本匹配**（防止重蹈历史 Bug 覆辙）
```bash
python3 skills/query-knowledge/scripts/query_knowledge.py \
    --repo . --backend predev --json \
    pd-scan --layer <frontend|backend|both> --tech <tech1> --file <path1>
```
从匹配的 Bug 模式提取「检测规则 / 预防措施 / 修复模板」，在设计文档的「风险点」章节引用。

对标 `docs/v2.1.0/设计文档.md` 格式，写入 `docs/vX.Y.Z/设计文档.md`：

1. **架构设计** — 模块划分、新增/修改文件清单、与现有架构关系（引用约束清单 + 契约规范）
2. **数据流设计** — 新增数据流路径、API 契约实现方案（引用契约规范中定义的端点）
3. **数据库变更**（如涉及）— 表结构/索引/迁移方案（引用契约规范中定义的数据模型）
4. **错误处理 + 兜底** — 必须符合 CLAUDE.md 三级降级体系（引用契约规范中定义的错误码）

**🚪 Gate:**
- 新增模块复用现有基类/接口/模式（不引入新模式除非有充分理由）
- 与 CLAUDE.md 不冲突
- 与契约规范 100% 对齐（设计文档中每个 API 必须能映射到契约规范中的端点）
- 无 TODO/TBD 占位符
- 🛑 **每张 Mermaid 图下方必须有 `📖 **图解说明**` 块**（5 板块：节点含义/关系含义/实施规则/边界情况）——参见上文"硬性规范"

### Phase 4: Planning Doc Generation

对标 `docs/v2.1.0/规划任务文档.md` 格式，写入 `docs/vX.Y.Z/规划任务文档.md`：

1. 从设计文档拆解可执行任务（粒度：单次对话可完成）
2. 标记依赖关系（阻塞 → 顺序执行，无依赖 → 可并行）
3. 分组：BE Worker 任务 / FE Worker 任务
4. 优先级（P0/P1/P2）+ 每任务估时

**🆕 v3.8 任务格式规范（checkbox 可执行追踪）**：

每个任务**必须**使用 `- [ ]` checkbox 格式，包含以下字段：

```markdown
- [ ] **Task N: [任务名]**（P0 · 估时 XXmin · 依赖：Task N-1 或 无）
  - **涉及文件**：`src/path/file.py`（修改/新增/删除）
  - **需求覆盖**：设计文档 §X.Y [章节名]
  - **验证点**：[可执行的验证命令或标准]
```

**格式规则**：
- `- [ ]` 前方不得有缩进（Markdown checkbox 标准语法）
- 任务名必须包含动词（"实现"/"修改"/"新增"/"删除"）
- 涉及文件必须用反引号包裹的完整路径
- 验证点必须是可执行命令或可测量标准，不得写"大概能跑就行"
- **禁止占位符**：不得出现 TBD/TODO/待定/后续补充

**示例**：

```markdown
- [ ] **Task 1: 新增 User 认证中间件**（P0 · 估时 30min · 依赖：无）
  - **涉及文件**：`src/middleware/auth.py`（新增）、`src/main.py`（修改）
  - **需求覆盖**：设计文档 §3.1 用户认证模块
  - **验证点**：`curl -H "Authorization: Bearer <token>" /api/me` 返回 200 + 用户信息

- [ ] **Task 2: 实现登录 API**（P0 · 估时 20min · 依赖：Task 1）
  - **涉及文件**：`src/api/auth.py`（新增）、`src/models/user.py`（修改）
  - **需求覆盖**：设计文档 §3.2 登录接口
  - **验证点**：`curl -X POST /api/auth/login -d '{"user":"test","pass":"123"}'` 返回 200 + JWT token
```

**🚪 Gate:**
- 每个设计点都有对应任务覆盖（覆盖率 100%）
- 无循环依赖
- 至少 1 组可并行任务
- 🛑 **任务依赖图必须有 `📖 **图解说明**` 块**——说明节点/关系/实施规则/边界情况
- 🆕 **所有任务使用 `- [ ]` checkbox 格式，包含涉及文件/需求覆盖/验证点三个必填字段**
- 🆕 **验证点全部可执行（不含模糊描述）**

> 💡 **可选**：任务规划完成后，调 `query-knowledge` 的 predev 后端检索历史错题本：
> ```bash
> python3 skills/query-knowledge/scripts/query_knowledge.py \
>     --repo . --backend predev --json \
>     pd-scan --layer <frontend|backend|both> --tech <tech1> --tech <tech2> --file <path>
> ```
> 根据变更范围（文件/技术栈/模式）匹配 Bug 模式，生成针对性预防清单。可在动手写代码前逐项确认。
> 注：本步整合了原 `pre-dev-checklist` skill 的能力，统一走 `query-knowledge` 入口。


### Phase 5: Self-Review + Change Level Assessment

#### 5.0 变更等级判定（必须在 Self-Review 之前）

Phase 4 完成后，根据设计文档的变更范围判定 **变更等级**（L1/L2/L3）。等级决定后续对抗审查策略：

| 等级 | 判定条件（满足任一即升级） | 对抗审查 |
|------|--------------------------|:--:|
| **L1** | 修改 ≤3 个文件，无新文件，无 API 变更，无算法引入 | ❌ 跳过 |
| **L2** | 新增/修改 4-10 个文件，或涉及 API 变更，或涉及中等复杂逻辑 | ⚠️ 快速审查 (5 维度) |
| **L3** | 新增/修改 >10 个文件，或引入新算法/数学运算/安全机制，或涉及流式/异步/缓存等复杂场景 | ✅ 完整审查 (7 维度) |

**自动升级规则**（以下任一条件满足 → 至少 L2）：
- 设计文档包含"相似度"/"hash"/"加密"/"缓存"/"流式"/"stream"关键词 → L2+
- 新增 `PUT`/`DELETE` 端点 → L2+
- 引入新的持久化机制（新文件格式/新 DB） → L2+
- 涉及 `asyncio.create_task` / `fire-and-forget` 等异步旁路 → L3
- 涉及数学公式或算法（Jaccard/cosine/加权投票等）→ L3
- 涉及多模型/多 provider 切换逻辑 → L3

#### 5.1 6 维度自评（原 Phase 5）

5 维度评分（复用 `design-review-optimization` 评分模型）：

| 维度 | 权重 | 检查 |
|------|------|------|
| 架构一致性 | 20% | 复用现有 DataSourceScheduler/IDataSource/CacheService？ |
| 需求覆盖率 | 20% | 需求点 + 边界 + 错误处理全覆盖？ |
| 契约对齐度 | 20% | 设计文档/实现与契约规范 100% 对齐？ |
| 数据完整性 | 15% | 表结构/索引/迁移完整？ |
| 历史设计对齐 | 15% | 三级降级/Provider/领域分离对齐？ |
| 可维护性 | 10% | 命名规范/测试策略/**图与图解齐全**？ |

> 🆕 **v0.17.1 注意**: 以上 6 维度的检查对象是**设计文档**和**契约规范**的文字内容,不包含对实际代码的机械验证。代码是否真正实现了契约中定义的每个事件/类型/错误码由下游 `deep-planning-rigorous-validation` 阶段8d 保证。

- **Score ≥ 85** → 通过，进入 Phase 5.2 (对抗审查)
- **Score < 85** → 标注问题，自动修正，最多 3 次
- **超 3 次** → 输出风险预警 `docs/vX.Y.Z/风险预警.md`，需人工介入

#### 🆕 Phase 5.4: 执行路由决策（输路由建议，自动路由仅在一键到底模式生效）

**为什么需要这一步骤**：传统 AI 工作流在设计完成后**总是**进入 `deep-planning-rigorous-validation`（单 Agent 串行），即使是大规模跨架构变更。但 L3 规模（>5 文件 + ≥3 设计决策）的任务由**单 Agent 串行执行**会导致：
- ❌ **耗时长**（14 个阶段逐一过，单 Agent 处理 30-45min）
- ❌ **上下文积累导致后期阶段决策质量下降**（Agent 在中间阶段开始"偷懒"跳过验证）
- ❌ **集成时发现跨模块断裂**（子 Agent × N 并行可避免）

**🎯 关键设计原则：自动路由仅在"一键到底"模式生效**

> **Phase 5.4 的核心职责是"输出路由建议"，而不是"强制自动路由"**。
> - **默认模式（需用户评审）**：Phase 5.4 输出路由建议，**让用户自己选择**走哪个执行器。保留用户对 L3 大型变更的最终决策权
> - **一键到底模式（全自动）**：Phase 5.4 输出路由建议，**Main Agent 自动按建议路由**到对应执行 Skill
> - 这种设计平衡了**自动化效率**和**用户控制权**——既不剥夺用户决策，又不要求每次都手动选

**两种模式对 Phase 5.4 的处理对比**：

| 模式 | Phase 5.4 输出 | 执行器选择 | 理由 |
|------|--------------|----------|------|
| **默认模式** | "变更等级: L3 大型 / 推荐: team-rigorous-delivery" | ⏸️ **用户手动选**（"执行" / "用 deep-planning" / "用 team"）| 保留用户决策权，**L3 大型变更让用户确认是否真要并行** |
| **一键到底** | "变更等级: L3 大型 / 自动路由: → team-rigorous-delivery" | 🤖 **Main Agent 自动按 L1/L2/L3 路由** | 无人值守效率最大化，**L3 自动启用 3 倍加速** |

**核心机制**（适用于一键到底模式的自动路由）：

| 变更等级 | 触发条件 | 执行路由 | 理由 |
|---------|---------|---------|------|
| **L1 微小** | 修改 ≤3 文件、无新文件、无 API/算法变更 | → `deep-planning-rigorous-validation`（单 Agent，**精简模式** 1→5→6→8）| 单 Agent 串行足够，避免多 Agent 协调开销 |
| **L2 中等** | 新增/修改 4-10 文件，或 API 变更，或中等复杂逻辑 | → `deep-planning-rigorous-validation`（单 Agent，**标准模式** 1→2→5→6→7→8→9）| 14 阶段流水线，完整 Gate 审计 |
| **L3 大型** | >10 文件，或新算法/安全/缓存/异步，或**满足任一自动升级规则** | → `team-rigorous-delivery`（**多 Agent 并行** 6 Wave + SubAgent × N）| 并行执行效率提升 ~3 倍，避免上下文积累 |

**默认模式下的用户决策界面**：

```
用户: "新增XXX功能"
        │
        ▼
feat-design-planning → Phase 5 评审通过 → Phase 5.4 路由建议
        │
        ▼  ⏸️  暂停，展示完整路由建议
"设计评审通过 (90分)
  变更等级: L3 大型（修改 12 文件、5 设计决策、跨 3 模块）
  
  📊 执行路由建议：
  - 推荐: → team-rigorous-delivery（多 Agent 并行，预期 15min）
  - 备选: → deep-planning-rigorous-validation（单 Agent 串行，预计 45min）
  - 备选: → 分阶段执行（先 User，再 Auth，最后 Order）
  
  💡 L3 优势：3 倍加速 + Wave Gate 跨 Wave 契约完整
  ⚠️ L3 风险：多 SubAgent 协调需要 Main Agent 持续监督
  
  请选择：
  - 说'执行' → 按推荐走 team-rigorous-delivery
  - 说'单 Agent' → 改用 deep-planning
  - 说'分阶段' → 改用分阶段单 Agent 执行
  - 说'修改XXX' → 调整设计"
```

**自动升级到 L3 的硬性条件**（以下任一满足 → 强制 L3）：

| 条件 | 示例 |
|------|------|
| 涉及数学公式/算法（Jaccard/cosine/加权投票/相似度/hash/加密） | 推荐系统的相似度计算 |
| 涉及 `asyncio.create_task` / fire-and-forget 等异步旁路 | 异步通知/队列消费 |
| 涉及多模型/多 provider 切换逻辑 | LLM 路由/多数据库切换 |
| 涉及流式/异步/缓存等复杂场景 | SSE 流式响应 + Redis 缓存 |
| 修改 >10 个文件 | 跨模块架构重构 |
| 涉及 ≥3 个独立失败域 | 多个互不依赖的子模块 |

**L3 额外判定**（满足任一 → L3）：
- 设计文档包含"相似度"/"hash"/"加密"/"缓存"/"流式"/"stream"等关键词
- 新增 `PUT`/`DELETE` 端点
- 引入新的持久化机制（新文件格式/新 DB）
- 涉及多模型/多 provider 切换逻辑
- 涉及数学公式或算法

**执行路由决策输出**：

在 `docs/vX.Y.Z/规划任务文档.md` 末尾必须包含"执行路由建议"章节：

```markdown
## 执行路由建议

**变更等级**: L3 大型
**触发条件**:
- 修改文件数: 12 个
- 设计决策数: 5 个
- 涉及模块: User, Auth, Order（跨 3 个模块）
- 包含关键词: "cache", "async", "stream"
- 数学算法: Jaccard 相似度计算

**执行路由**: → team-rigorous-delivery

**理由**:
- ✅ 满足自动升级条件（数学算法 + 异步旁路 + 跨模块）
- ✅ 满足文件数阈值（>10 文件）
- ✅ 满足多失败域条件（≥3 独立失败域）

**子 Agent 切片建议**（5-7 个并行切片）:
1. **Wave 1**: User 模块（3 个文件）
2. **Wave 1**: Auth 模块（4 个文件）
3. **Wave 1**: Order 模块（3 个文件）
4. **Wave 2**: 集成 + 契约 wiring 验证
5. **Wave 3**: 端到端测试 + 性能验证

**预期加速**: 单 Agent 串行 ~45min → 多 Agent 并行 ~15min（3 倍加速）
```

**关键原则**：
- ✅ **只可降级不可升级**：L1 中途发现需要 L3 → 必须回 Phase 5.0 重新判定
- ✅ **Main Agent 显式路由**：不依赖 SubAgent 自己判断，避免模糊
- ✅ **设计阶段就锁定执行路径**：评审通过后立即确定，避免 deep-planning 阶段做选择
- ✅ **跨 Wave 契约 wiring**：L3 路径必须由 Main Agent 在 Wave 1 完成后执行 `Wave Gate`（轻量 grep + import 烟测）

**自动降级规则**（防止 L3 误判导致不必要的多 Agent 协调）：
- 判定阈值有疑问 → 默认走 L2（标准模式），保守但稳妥
- 多 Agent 协调失败 >2 次 → 自动降级为单 Agent
- 切片文件数实际 < 5 → 自动降级为 L2

**与 v3.6 grill-me 的协同**：
- Phase 0.5 grill-me 已完成需求对抗
- Phase 5.4 执行路由决策基于已确定的需求 + 设计
- 不需要 grill-me 介入执行路由（路由是机械化判定，不是协商）

#### 🆕 5.2 Adversarial Review — 独立上下文对抗审查

**为什么需要这一步骤**：Self-Review（Phase 5.1）是「设计者自评」，共享同一上下文，存在**设计者偏见**——设计者天然认为自己的设计是正确的。Adversarial Review 用**独立 Agent**（干净上下文）从攻击者/极端场景/方法论的角度审视设计，消除偏见。

**触发条件**（由 Phase 5.0 变更等级决定）：

| 等级 | 触发 | 审查维度 | 最大修复轮数 |
|------|:--:|---------|:----------:|
| **L1** | ❌ 跳过 | — | — |
| **L2** | ✅ 快速审查 | 5 维度 | 1 轮 |
| **L3** | ✅ 完整审查 | 7 维度 | 3 轮 |

**执行方式**：使用 `Agent` 工具 spawn 一个独立的 SubAgent，**仅传入设计文档路径和项目基线**，不传入分析过程的对话历史。

**L2 快速审查维度（5 维度）**：

| 维度 | 检查 |
|------|------|
| 覆盖盲区 | 流式/非流式两个分支、错误路径、配置缺失 是否有对应设计？ |
| 安全审计 | 新增端点是否需要认证？`PUT`/`DELETE` 是否有权限保护？敏感数据是否暴露？ |
| 生产就绪 | 有功能开关吗？有回滚方案吗？有监控指标吗？ |
| 逻辑漏洞 | 决策逻辑是否在边界条件下会产生死循环/空指针/除零？ |
| 依赖完整 | 所有跨模块引用是否已定义？所有配置项是否有默认值？ |

**L3 完整审查维度（7 维度）** = L2 5 维度 + 以下 2 维度：

| 维度 | 检查 |
|------|------|
| 🆕 算法正确性 | 任何涉及相似度/hash/数学公式/加权/排序的操作，语义是否正确？输入输出是否定义了？ |
| 🆕 性能边界 | 最差情况分析：1000条记录时 O(n²) 是否会阻塞？并发写入是否有竞态？内存上限？ |

**审查输出格式**：

```markdown
# Adversarial Review Report

## 审查等级: L3 (完整审查)

## 发现问题

### 🔴 Block (必须修复)
| # | 维度 | 问题 | 建议 |
|---|------|------|------|

### 🟡 High (建议修复)
| # | 维度 | 问题 | 建议 |
|---|------|------|------|

### 🟢 Low (可后续优化)
| # | 维度 | 问题 | 建议 |
|---|------|------|------|

## Overall: Pass / Pass with Comments / Block
```

**修复循环**：

```
Adversarial Review 输出
  │
  ├─ Block 问题 = 0 → Phase 5.3 最终评审
  │
  ├─ Block 问题 > 0 → 修复设计文档 → 重新 Self-Review → 重新 Adversarial Review
  │     循环次数由变更等级决定:
  │       L2: 最多 1 轮
  │       L3: 最多 3 轮
  │
  └─ 超限 → 输出风险预警，标记「已知未修复的 Block 问题」，需人工介入
```

**🛑 SubAgent 构建规则**：

Adversarial Review SubAgent prompt 必须严格按以下模板构建：

```markdown
## 任务: 对 vX.Y.Z 设计文档进行对抗审查

你是一个独立的安全/架构审计 Agent。你的任务是**攻击这份设计**——找出自评无法发现的漏洞。
你不参与设计，你只负责**质疑一切**。

### 审查等级: {L2/L3}

### 必读文档（SubAgent 自行 Read）
1. `docs/vX.Y.Z/契约规范.md`
2. `docs/vX.Y.Z/设计文档.md`
3. `docs/vX.Y.Z/规划任务文档.md`
4. `CLAUDE.md` — 项目架构约束

### 审查维度: {根据等级列出}

### 审查原则
- 假设设计中有你没发现的错误，你的任务是找到它们
- 对任何涉及数学/算法/相似度/hash/cache 的操作，在草稿纸上手动推演
- 对任何端点，问「没有认证的情况下，外部能调用吗？」
- 对流式/非流式两个代码分支，对比它们的设计覆盖
- 对任何「估计/估算」的值，追问「来源是什么？边界是什么？」
- 输出必须按 Block/High/Low 分级，Block 问题必须是不可妥协的

### 输出格式
{见上方审查输出格式模板}
```

#### 5.3 最终评审报告

Phase 5.2 通过后（无 Block 问题），生成最终评审报告 `docs/vX.Y.Z/评审报告.md`：

**评审报告必须包含**：
1. 变更等级 + 审查等级
2. 6 维度 Self-Review 评分
3. Adversarial Review 结果（如触发）
4. 修订记录（如有修复循环）
5. 最终结论 + 契约 wiring 验证提醒

### 🆕 v0.17.1 契约 wiring 预留校验（设计阶段不可省略）。

Phase 5 评审通过后，**必须在评审报告末尾加入以下声明**（提醒下游 deep-planning 阶段8d 执行机械验证）：

```markdown
## ⚠️ 契约 wiring 验证提醒

本评审仅验证了**设计文档**和**契约规范**的内部一致性（如字段定义、错误码、事件名）。
**未验证代码是否真正实现了契约中定义的每个事件/类型/错误码**。

以下契约项需在 deep-planning-rigorous-validation **阶段8d（契约一致性审计）**中进行机械 grep 验证：

1. 契约规范中定义的每个 SSE 事件 → 代码中是否有对应的 `yield {"type": "..."} ` 发射端
2. 契约规范中定义的每个类型 → 前端 TypeScript 是否有 `interface`/`type` 定义 + 后端 Python 是否有对应 `dataclass`
3. 契约规范中定义的每个错误码 → 代码中是否有对应的 raise/return
4. 契约规范中定义的每个配置项 → config.yaml + Pydantic model 是否已注册

**由 deep-planning 阶段8d 保证覆盖率 ≥95%，否则不得合并。**
```

## Output Directory

本 Skill 与 `deep-planning-rigorous-validation` 共用同一输出目录，形成完整的设计→执行文档链：

```
docs/v<version>/
├── 契约规范.md               # Phase 2 产出 — SDD 前后端契约 (feat-design-planning)
├── 设计文档.md               # Phase 3 产出 (feat-design-planning)
├── 规划任务文档.md            # Phase 4 产出 (feat-design-planning)
├── 评审报告.md               # Phase 5 产出 (feat-design-planning)
├── 01-requirements.md        # deep-planning 阶段1
├── 02-impact-analysis.md     # deep-planning 阶段2 (L2+)
├── 03-dataflow.md            # deep-planning 阶段3 (L3)
├── 04-design-review.md       # deep-planning 阶段4 (L3)
├── 05-implementation-plan.md # deep-planning 阶段5
├── 06-self-check.md          # deep-planning 阶段6
├── 07-api-e2e-test.md        # deep-planning 阶段7 (L2+, 🚨硬性门禁)
├── 07b-browser-test.md       # deep-planning 阶段7b (L3+前端变更)
├── 08-code-review.md         # deep-planning 阶段8
└── 09-integration-report.md  # deep-planning 阶段9 (L2+)
```

版本号自动递增：检测现有 `docs/v*.*/` 目录取最大版本号 +1。`deep-planning-rigorous-validation` 会复用本 Skill 创建的目录。

## 执行模式

本 Skill 支持两种执行模式，在 Phase 0 启动时根据用户措辞自动判定：

### 默认模式（需用户评审）

**触发**：用户正常描述需求，未使用一键到底关键词。

**行为**：Phase 5 评审通过后**暂停**，展示设计文档摘要 + 评审结果 + **执行路由建议**，**让用户自己选择**走哪个执行器（不自动路由）。

```
用户: "新增XXX功能"
        │
        ▼
feat-design-planning → Phase 5 评审通过 → Phase 5.4 路由建议
        │
        ▼  ⏸️  暂停，展示完整路由建议
"设计评审通过(90分)，文档在 docs/v2.3.0/

  📊 变更等级: L3 大型（修改 12 文件、5 设计决策、跨 3 模块）

  🎯 执行路由建议：
  - ✅ 推荐: team-rigorous-delivery（多 Agent 并行，预期 15min，加速 3 倍）
  - 🔄 备选: deep-planning-rigorous-validation（单 Agent 串行，预计 45min）
  - 🔄 备选: 分阶段单 Agent（User → Auth → Order）

  💡 L3 优势：3 倍加速 + Wave Gate 跨 Wave 契约完整
  ⚠️ L3 风险：多 SubAgent 协调需 Main Agent 持续监督

  请选择：
  - 说'执行' → 按推荐走 team-rigorous-delivery
  - 说'单 Agent' → 改用 deep-planning
  - 说'分阶段' → 改用分阶段单 Agent
  - 说'修改XXX' → 调整设计"
```

### 🆕 v3.4 SubAgent 隔离架构

**核心原则**：需求分析阶段（feat-design-planning）和代码实现阶段（deep-planning）运行在**不同的 SubAgent** 中，使用干净的独立上下文。需求分析的反复试探、否决方案、评分细节**不污染**代码实现阶段的上下文。

```
用户会话 (Main Agent)
  ├── feat-design-planning-v3 → 产物: 产出文档包
  └── spawn SubAgent → deep-planning-rigorous-validation-v3 (干净上下文)
```

**Phase 跃迁隔离边界**：

| 跃迁 | 隔离方式 | 传入内容 | 不传入内容 |
|------|---------|---------|-----------|
| 需求分析 → 代码实现 | Agent 工具 spawn SubAgent | 产出文档路径 + 项目基线 | 分析过程的对话历史、试探提问、否决方案、评分讨论 |
| 代码实现 → Bug 修复 | Agent 工具 spawn SubAgent | Gate 失败描述 + 相关文件路径 + 项目基线 | 需求分析全过程 + 代码实现全过程 |

**上下文污染消除效果**：
- SubAgent 的上下文从 ~3000+ 行对话历史降到 ~300 行（文档内容 + 项目基线）
- SubAgent 只看到**最终确定的契约/设计/规划文档**，看不到分析过程
- 遵循 Karpathy「精准修改」原则：不被干扰信息影响判断

---

### 一键到底模式（全自动）

**触发关键词**（用户需求中包含任一即激活）：
- `一键到底` / `全自动` / `一口气完成` / `从头到尾`
- `不需要确认` / `直接做完` / `自动完成`
- `auto` / `end-to-end` / `no-confirm`

**行为**：Phase 5.4 执行路由决策完成后，**通过 `Agent` 工具 spawn 干净的 SubAgent** 执行代码实现阶段。**SubAgent 的类型根据 Phase 5.4 决策动态选择**：

| 变更等级 | SubAgent Skill | 并行数 | 预期时间 |
|---------|---------------|-------|---------|
| L1/L2 | `deep-planning-rigorous-validation-v3` | 1 个单 Agent | 10-25min |
| L3 | `team-rigorous-delivery-v3` | 多 SubAgent × N | 15-30min（vs 单 Agent 45min）|

**L1/L2 流程**（单 Agent 串行）：
```
用户: "新增XXX功能，一键到底"
        │
        ▼
feat-design-planning-v3 (Main Agent)
  Phase 0-5.4: 需求分析 → SDD → 设计 → 评审 → 路由决策
        │
        │  📢 "[Main Agent] ✅ 设计评审通过 (92分)"
        │  📢 "[Main Agent] 📦 产出文档: docs/vX.Y.Z/"
        │  📢 "[Main Agent] 🔀 变更等级: L2 → deep-planning-rigorous-validation"
        │  📢 "[Main Agent] 🚀 启动代码实现 SubAgent (干净上下文)..."
        │
        ▼  spawn Agent tool (skill="deep-planning-rigorous-validation-v3")
  ┌─────────────────────────────────────────┐
  │ SubAgent: deep-planning-rigorous-      │
  │           validation-v3 (单 Agent)      │
  │                                         │
  │ 输入: docs/vX.Y.Z/*.md + CLAUDE.md     │
  │       + config.yaml + src/ 项目基线     │
  │                                         │
  │ Stage 1-10: 需求重述 → ... → 交付      │
  │                                         │
  │ 📢 "[SubAgent] 阶段1: 需求重述完成"     │
  │ 📢 "[SubAgent] 阶段5: 实现计划完成"     │
  │ 📢 "[SubAgent] 阶段7: E2E 验证通过 ✅"  │
  │ 📢 "[SubAgent] 阶段9: 集成报告完成 ✅"  │
  └─────────────────────────────────────────┘
        │
        │ SubAgent 返回结果给 Main Agent
        ▼
  📢 "[Main Agent] ✅ 功能开发完成"
  📢 "[Main Agent] 建议使用 openspec-archive-change 归档"
```

**L3 流程**（多 Agent 并行）：
```
用户: "新增XXX大型功能，一键到底"
        │
        ▼
feat-design-planning-v3 (Main Agent)
  Phase 0-5.4: 需求分析 → SDD → 设计 → 评审 → 路由决策
        │
        │  📢 "[Main Agent] ✅ 设计评审通过 (90分)"
        │  📢 "[Main Agent] 📦 产出文档: docs/vX.Y.Z/"
        │  📢 "[Main Agent] 🔀 变更等级: L3（12 文件、5 决策、跨 3 模块）"
        │  📢 "[Main Agent] 🚀 启动 team-rigorous-delivery (多 SubAgent 并行)..."
        │
        ▼  spawn Agent tool (skill="team-rigorous-delivery-v3")
  ┌─────────────────────────────────────────┐
  │ Main Agent: team-rigorous-delivery-v3  │
  │   持有全局视图 + Wave Gate 审计          │
  │                                         │
  │ Wave 1: SubAgent × N 并行执行切片      │
  │   ├─ SubAgent 1: User 模块（3 文件）  │
  │   ├─ SubAgent 2: Auth 模块（4 文件）  │
  │   └─ SubAgent 3: Order 模块（3 文件） │
  │   契约 wiring 自检 (Mini-6.6)         │
  │   ↓                                    │
  │ Wave Gate: 跨 Wave 契约完整性检查      │
  │   - 识别跨 Wave 共享文件                │
  │   - 机械 grep 验证契约 wiring          │
  │   - import 冒烟测试                     │
  │   - 受影响测试套件                      │
  │   ↓                                    │
  │ Wave 2: 集成 + 端到端 + 性能验证       │
  │                                         │
  │ 📢 "[SubAgent-1] User 模块完成"        │
  │ 📢 "[SubAgent-2] Auth 模块完成"        │
  │ 📢 "[SubAgent-3] Order 模块完成"       │
  │ 📢 "[Main Agent] Wave Gate ✅ 全部通过" │
  │ 📢 "[Main Agent] Wave 2 集成完成 ✅"    │
  └─────────────────────────────────────────┘
        │
        ▼
  📢 "[Main Agent] ✅ L3 大型变更完成 (并行 15min vs 串行 45min)"
  📢 "[Main Agent] 加速效果: 3 倍"
```

### 🆕 v3.4 Phase 5+: 一键到底 — SubAgent 隔离交付步骤

Phase 5 评审通过后，必须按以下步骤执行（不可直接 inline 继续）：

**Step 5+.1: 验证产出文档完整性**

确认以下文件已存在且非空：
```bash
ls -la docs/vX.Y.Z/契约规范.md docs/vX.Y.Z/设计文档.md docs/vX.Y.Z/规划任务文档.md docs/vX.Y.Z/评审报告.md
```

**Step 5+.2: 打印交接进度**

向用户打印明确的进度信息（不隐藏）：

```
📢 [Phase 5+] 设计阶段完成，准备进入代码实现阶段
   ├── ✅ 契约规范: docs/vX.Y.Z/契约规范.md
   ├── ✅ 设计文档: docs/vX.Y.Z/设计文档.md
   ├── ✅ 规划任务: docs/vX.Y.Z/规划任务文档.md
   ├── ✅ 评审报告: docs/vX.Y.Z/评审报告.md (评分: XX/100)
   └── 🚀 启动代码实现 SubAgent（独立干净上下文）...
```

**Step 5+.3: 构建 SubAgent 启动包**

使用 `Agent` 工具 spawn SubAgent，prompt 必须严格按照以下模板构建（禁止传入分析过程对话历史）：

```markdown
## 任务: 实现 vX.Y.Z 功能

你是一个独立的代码实现 Agent，使用 `deep-planning-rigorous-validation-v3` skill。
你的输入是已确定的**设计文档和契约规范**，而非需求分析过程。
你不需要重新评估设计决策是否正确，只需要按设计实现。

### 必读文档（按顺序，SubAgent 自行 Read）
1. `docs/vX.Y.Z/契约规范.md` — SSE 事件契约 + 类型定义 + 错误码 + 前后端责任边界
2. `docs/vX.Y.Z/设计文档.md` — 架构设计 + 数据流 + 模块设计 + 三级降级体系
3. `docs/vX.Y.Z/规划任务文档.md` — 任务拆解 + 依赖关系 + Phase 顺序 + 估时
4. `docs/vX.Y.Z/评审报告.md` — 已知风险点 + 扣分项 + 契约 wiring 验证提醒
5. `CLAUDE.md` — 项目架构约束 + 关键设计决策 + 测试策略

### 项目基线
- 代码目录: `src/` (后端 Python/FastAPI) + `frontend/src/` (前端 Vue 3/TypeScript)
- 测试目录: `tests/`
- 配置文件: `config.yaml`
- 运行命令: `pytest tests/ -v` / `cd frontend && npm run dev`

### 约束
- 遵循 CLAUDE.md 中的所有架构约束
- 遵循契约规范中的所有接口定义，不得偏离
- 遵循设计文档中的三级降级体系 (L1/L2/L3)
- 遵循规划任务文档中的 Phase 顺序
- **禁止根据对话历史推测任何未经文档确认的设计细节**

### 执行
请使用 `deep-planning-rigorous-validation-v3` skill 执行完整的代码实现流程，
从阶段 1（需求重述）开始到阶段 10（文档交付）。

### 进度报告
每个阶段完成后向 Main Agent 报告进度（如 "📢 [SubAgent] 阶段N: XXX 完成"），
让 Main Agent 可以打印给用户看。
```

**Step 5+.4: 监控 SubAgent 执行进度**

Main Agent 在收到每阶段完成后**打印进度给用户**。不隐藏、不合并。

**Step 5+.5: 处理 SubAgent 完成/失败**

- SubAgent 完成 → 📢 打印完成摘要 → 提示归档
- SubAgent 超限/失败 → 📢 打印失败原因 → 暂停，报告用户

### 🆕 v3.4 自动衔接规则（SubAgent 隔离版）

| 衔接点 | 触发条件 | 动作 | 隔离方式 |
|--------|---------|------|---------|
| 设计→执行 | Phase 5 Score ≥ 85 | 🚀 spawn SubAgent（干净上下文） | Agent 工具，仅传文档路径 + 项目基线 |
| 执行→Bug修复 | deep-planning Gate 不通过 | 🔧 spawn fix-bug SubAgent（干净上下文） | Agent 工具，仅传 Gate 失败描述 + 相关文件 |
| 修复→重验证 | Bug 已修复 | SubAgent 自行重跑对应 Gate | SubAgent 内部循环，无需重新 spawn |
| 修复超限 | 单 Gate ≥3 次失败 | ⚠️ SubAgent 返回失败 → Main Agent 暂停 | Main Agent 报告用户 |
| 完成→归档 | 阶段9 通过 | 📢 Main Agent 提示 `openspec-archive-change` | - |

### 打断自动链

即使在自动模式中，以下情况会暂停：
- 需要用户输入（如数据库密码、API 密钥）
- 同一门禁 3 次不通过（需人工判断）
- 涉及高风险操作（数据库迁移、删除数据）
- SubAgent spawn 失败（环境问题、skill 未安装等）

## Related Skills

| Skill | 关系 |
|-------|------|
| `design-review-optimization` | Phase 5.1 评分模型来源 |
| `superpowers:brainstorming` | Phase 1 头脑风暴方法 |
| `andrej-karpathy-skills:karpathy-guidelines` | 核心准则（思考→简洁→精准→目标驱动） |
| `deep-planning-rigorous-validation-v3` | 下游：深度规划 + 严格验证执行 (L1/L2/L3 自适应) (v3.4 起通过 SubAgent 隔离) |
| `fix-bug-workflow` | 下游：修复执行中的 Bug (v3.4 起通过 SubAgent 隔离) |
| `team-rigorous-delivery-v3` | 下游：L3 大规模变更的团队并行执行 (v3.4 统一 SubAgent 隔离模式) |
| `openspec-archive-change` | 下游：归档完成变更 |
| `pre-dev-checklist` | 已整合：能力并入 `query-knowledge --backend=predev`，统一检索入口 |
| 🆕 `query-knowledge` | Phase 0 KB 索引 + Phase 1 L3 按需检索 + Phase 4 错题本匹配，统一知识背景检索入口 |
| 🆕 `Agent (general-purpose)` | Phase 5.2 对抗审查 SubAgent 的执行载体 (独立上下文，消除设计者偏见) |

## Red Flags

- 跳过了 Phase 0 直接开始设计 → 设计可能违背现有架构
- 涉及前后端协作但跳过了 Phase 2 (SDD) → 前后端契约不一致，集成返工风险极高
- 需求边界模糊就进入 Phase 3 → 返工风险极高
- 评审分数不达标但仍然继续 → 设计质量无保障
- "这个需求很简单不需要设计" → 简单需求也会因未检查约束而踩坑
- **🆕 设计文档全是文字没有图** → 复杂架构/数据流难以传达，code review 时容易理解偏差
- **🆕 流程图下面没有 `📖 **图解说明**`** → 图成为"装饰"，无法作为实施规则，下次迭代容易走偏
- **🆕 v0.17.1 设计评审通过 ≠ 代码实现完备** → Phase 5 评审只验证「设计文档内部一致性」，不验证「代码是否真正发射了契约中定义的每个事件/消费了每个类型」。此问题在 deep-planning-rigorous-validation 阶段8d「契约一致性审计」中由机械 grep 验证兜底。评审报告通过后必须提醒下游: **「本评审未验证代码 wiring，契约项的实际实现完备度由 deep-planning 阶段8d/8e 保证」**
- **🆕 v3.5 L3 变更跳过 Adversarial Review** → 涉及新算法/数学/缓存的复杂功能必须跑对抗审查。设计者自评存在「设计者偏见」——天然认为自己设计正确，无法发现方法论级别错误
- **🆕 Adversarial Review Block 问题未修复就进入实现** → Block 问题是不可妥协的（算法错误/安全漏洞/流式缺失），必须在设计阶段解决，不能带到代码阶段
- **🆕 对抗审查使用了设计者相同的上下文** → 必须用 `Agent` 工具 spawn 独立 SubAgent，仅传文档路径 + 项目基线。共享上下文会污染审查者的判断，失去「独立视角」的价值
