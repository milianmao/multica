---
name: control-plane-session
version: 1.1.0
description: >-
  Claude Code 会话开工登记（多轮维度访谈）— 在跑本仓业务 Skill 之前，通过多轮提问
  收集目标/范围/风险/成功标准等维度，再一键写入本地 control_plane TaskRun 并路由，
  输出 TASK_ID 交接块。触发词：会话开始、登记任务、开工、session start、
  /session-start、/control-plane-session、先注册再干活、任务维度访谈。
  不写浏览器状态；不替代 feat-design-planning / deep-planning 等业务 Skill。
evaluated_by:
  - CPS-E-001
  - CPS-M-001
---

# control-plane-session — 会话开工登记（v1.1 · 多轮维度访谈）

## 定位

把 **Claude Code 对话** 与 **v5.0 本地 control_plane 账本** 接上，并且登记信息要**足够清晰**：

```text
会话开始
  → 多轮询问任务维度（一次一问或一小批）
  → 汇总 dimensions JSON
  → session start（create + route + 落盘 intake.session.json）
  → HANDOFF（TASK_ID + 目标/成功标准）
  → 再跑业务 Skill
```

- **是**：本地登记 + **结构化访谈** + 路由 + 交接  
- **不是**：中心登录、浏览器写状态、自动监控每一次 `/skill`

## When to Use

| 场景 | 行为 |
|------|------|
| 新开会话要干工程活 | **先本 Skill 访谈并登记**，再业务 Skill |
| 「登记任务 / 开工 / session start」 | 进入访谈 → 登记 |
| 用户已甩出完整需求 | 可推断的维度**勿重复问**，只补缺口 |
| 仅闲聊不改仓 | **不要**登记 |

## 硬规则

1. CLI 唯一写入；禁止手改 `runs/**` 伪装登记。  
2. **默认 `interview_mode=full`**：至少覆盖核心维度（见下）再 `session start`。  
3. 用户说「快速开工 / quick」→ `interview_mode=quick`（3 问）。  
4. 用户说「只开个号 / minimal」→ 仅 title（会 warning，不推荐）。  
5. 登记成功必须输出 **HANDOFF**；未成功不得声称「已纳入控制面」。  
6. 一次**只问 1 个维度主题**（或紧密相关的 2 个小问），避免问卷轰炸。  
7. 用户已回答过的内容**禁止再问**；从首条消息推断并回显确认。

---

## 维度模型（写入 `session.dimensions`）

| 键 | 含义 | full | quick | 示例问法 |
|----|------|:----:|:-----:|----------|
| `goal` | 要解决什么 / 交付什么 | **必问** | **必问** | 「用一句话说本次要交付什么？」 |
| `background` | 为何现在做 | 建议 | — | 「背景或触发原因是什么？」 |
| `scope_in` | 范围内（列表） | **必问** | 建议 | 「明确做哪些点？（可列 3～5 条）」 |
| `scope_out` | 非目标（列表） | 建议 | — | 「明确不做哪些，避免范围膨胀？」 |
| `users` | 使用者/受益方 | 建议 | — | 「主要给谁用 / 谁验收？」 |
| `success_criteria` | 可验证成功标准 | **必问** | **必问** | 「怎样算做完？给 1～3 条可检查标准。」 |
| `affected_paths` | 影响路径/模块 | 建议 | — | 「可能动到哪些目录/模块？」 |
| `risk_signals` | 风险信号 | 建议* | 风险相关必问 | 「有无权限/隐私/契约/生产/并行切片？」 |
| `constraints` | 约束 | 建议 | — | 「时间、兼容、禁止改的区域？」 |
| `dependencies` | 依赖（人/系统） | 可选 | — | 「依赖谁审批或哪个外部系统？」 |
| `deliverables` | 交付物 | 建议 | 可选 | 「除代码外还要文档/测试/脚本吗？」 |
| `notes` | 其它 | 可选 | — | 「还有必须记下的吗？」 |

\* 若已出现 security/契约/大切片信号，必须追问确认并反映到 `--risk-profile`。

另外仍要收集（可不单独成 dimensions 键）：

| 字段 | 选项 | 默认 |
|------|------|------|
| `title` | 短标题 | 从 goal 压缩 |
| `change_type` | feature/bugfix/docs/refactor/chore/hotfix | feature |
| `risk_profile` | fast/standard/deep/team/high_risk | 由风险信号推断，不确定选更严 |
| `actor` | 花名 | git user / 用户自称 |

**risk_profile 映射**

| 信号 | profile |
|------|---------|
| 文档/单点 | `fast` |
| 常规功能/修 bug | `standard` |
| 跨模块/契约/多文件 | `deep` |
| 可并行大切片 | `team` |
| 权限/隐私/安全/资金/生产迁移 | `high_risk` |

---

## Process

### Step 0 — 预检

```bash
python -m control_plane registry sync --write
```

已同步可跳过。

### Step 1 — 选择访谈模式

| 用户说法 | mode |
|----------|------|
| 默认 / 登记 / 开工 | `full` |
| 快速开工、少问点 | `quick` |
| 只开 ID、稍后补 | `minimal`（仅 title；会 warning） |

向用户**一句话说明**当前模式，例如：  
「按完整模式登记，我会分几轮问目标、范围和成功标准。」

### Step 2 — 多轮访谈（核心）

#### 2.1 启动方式

- 若用户首条已含 goal/范围/风险 → **先复述确认表**，只问缺失项。  
- 否则按顺序问，**每轮 1 题**（或「范围内 + 非目标」捆成一轮）。

#### 2.2 full 推荐轮次（最多 6～8 轮，可提前结束）

```text
R1  goal            → 一句话目标
R2  change_type     → 功能 / 修 bug / 文档 / 重构…
R3  scope_in + scope_out
R4  success_criteria（1～3 条可验证）
R5  risk_signals + 推断 risk_profile（给用户确认）
R6  affected_paths 和/或 constraints（有则问）
R7  deliverables + users（可选）
R8  总表确认 → 进入登记
```

#### 2.3 quick（3 轮）

```text
R1 goal/title
R2 change_type + risk_profile
R3 success_criteria
→ 登记
```

#### 2.4 每轮输出格式（对用户）

```text
【登记访谈 R{n}/{mode}】
问题：……
（可选）我根据你刚才说的，暂记：……
请直接回答；回「跳过」则本维记为空。
```

#### 2.5 内存中维护草稿（勿落盘到权威状态目录）

Agent 在上下文中维护：

```json
{
  "title": "",
  "change_type": "feature",
  "risk_profile": "standard",
  "actor": "",
  "summary": "",
  "dimensions": {
    "goal": "",
    "background": "",
    "scope_in": [],
    "scope_out": [],
    "users": [],
    "success_criteria": [],
    "affected_paths": [],
    "risk_signals": [],
    "constraints": [],
    "dependencies": [],
    "deliverables": [],
    "notes": ""
  }
}
```

列表维可用逗号/分号/换行，CLI 会规范化。

#### 2.6 确认表（登记前必须展示）

```text
——— 登记前确认 ———
标题：…
类型：… | 风险档：…
目标：…
范围内：…
非目标：…
成功标准：…
影响路径：…
风险信号：…
交付物：…
约束：…
————————————
回复「确认登记」继续；或指出要改的字段。
```

未确认不得执行 `session start`（除非用户明确「直接登记不用确认」）。

### Step 3 — 写入 dimensions 文件并登记

1. 将 `dimensions` 写入仓库内临时文件（**允许**，非权威状态机）：

```bash
# 示例路径（可按 TASK 草稿名）
mkdir -p control-plane-state/session-drafts
# 写入 dimensions JSON 到例如：
# control-plane-state/session-drafts/last-dimensions.json
```

2. 执行权威登记：

```bash
python -m control_plane --json --actor "<actor>" session start \
  --title "<title>" \
  --summary "<summary 可选>" \
  --change-type <feature|bugfix|docs|refactor|chore|hotfix> \
  --risk-profile <fast|standard|deep|team|high_risk> \
  --interview-mode <full|quick|minimal> \
  --dimensions-file control-plane-state/session-drafts/last-dimensions.json \
  --export-ui
```

等价：

```bash
  --dimensions-json '{"goal":"...","success_criteria":["..."],"scope_in":["..."]}'
```

**成功**：exit 0，`ok: true`，`data.task_id` 有值，`data.status`/`route_mode` 合理。  
查看 `data.dimensions`、`data.dimensions_filled`、`warnings`（缺维会提示）。

**落盘（权威）**

- `control-plane-state/runs/<TASK_ID>/task.json`
- `control-plane-state/runs/<TASK_ID>/route-decision.json`
- `control-plane-state/runs/<TASK_ID>/intake.session.json` ← **含完整 dimensions**

### Step 4 — HANDOFF（必须展示）

优先用 CLI 返回的 `data.handoff`，并补充维度摘要：

```text
——— CONTROL PLANE HANDOFF ———
TASK_ID=…
TITLE=…
GOAL=…
ROUTE_MODE=…
ACTOR=…
SUCCESS=… | …
OUT_OF_SCOPE=… | …
PATHS=… | …
NEXT_SKILLS=…
DIMENSIONS_FILLED=goal,scope_in,success_criteria,…
RULE=后续阶段完成必须 control_plane 回写；未回写不得声称已纳入控制面。
VIEW=#task/<TASK_ID>
——— END HANDOFF ———
```

### Step 5 — 交接业务 Skill

| 场景 | 下一步 |
|------|--------|
| feature + standard/deep | `/feat-design-planning` → `/deep-planning-rigorous-validation` |
| bugfix | `/fix-bug-workflow` |
| team | `/team-rigorous-delivery` |
| docs | 文档/KB；写 project-note 前 `/sensitive-info-filter` |
| high_risk | 深链路 + **独立审批** 才能 deliver |

后续提示必须带 `TASK_ID`，并可引用 `intake.session.json` 中的 dimensions，避免业务 Skill 再从零问一遍目标。

### Step 6 — 阶段回写提醒

```bash
python -m control_plane --actor "<actor>" task start-stage \
  --task-id <TASK_ID> --stage design --skill feat-design-planning --attempt 1
# …
python -m control_plane --actor "<actor>" evidence add \
  --task-id <TASK_ID> --type doc --path docs/... --source session
python -m control_plane ui export
```

---

## 用户话术示例

**完整访谈**

```text
/session-start
我要做导出任务报告
```

Agent：进入 full，分轮问 goal/范围/成功标准/风险 → 确认表 → 登记。

**快速**

```text
快速开工：修登录页样式，标准风险，成功=视觉与设计稿一致
```

Agent：quick 模式，补齐后直接确认登记。

**用户一次给全**

```text
登记：跨模块加导出 API；范围内 export CLI+UI；非目标不做权限中心；
成功=有单测且工坊能看到；风险含契约变更；deep
```

Agent：填 dimensions，只确认表，不再逐项追问。

---

## 与其它 Skill

| Skill | 关系 |
|-------|------|
| feat-design-planning | 下游；可复用 dimensions 作 Phase 1 输入 |
| deep-planning / fix-bug / team-* | 下游执行 |
| seed closed-loop | 演示样例，**不是**会话登记 |

## Red Flags

- ❌ 不问就登记（full 模式缺 goal/成功标准）又声称「记录很清晰」  
- ❌ 一次抛 10 个问题  
- ❌ 已回答还重复问  
- ❌ 跳过确认表强行 start（用户未授权）  
- ❌ 用 SAMPLE 任务冒充本次会话  

## 验证

```bash
python -m control_plane --dry-run --json session start \
  --title "试跑" --interview-mode full \
  --dimensions-json '{"goal":"试跑","success_criteria":["有TASK_ID"],"scope_in":["session"]}'

python -m unittest control_plane.tests.test_session_start -v
```

## 版本

- v1.0.0：session start CLI + 基础 Skill  
- v1.1.0：多轮维度访谈协议 + `--dimensions-file` / `--dimensions-json` + intake 结构化落盘
