---
last_updated: 2026-11-14
source_files:
  - server/internal/service/issue_trigger.go
  - server/internal/service/task.go
  - server/internal/handler/daemon.go
  - server/internal/dispatch/reason.go
  - server/internal/scheduler/
  - server/cmd/server/runtime_sweeper.go
  - server/pkg/db/queries/agent.sql
  - server/pkg/taskfailure/
  - packages/core/realtime/use-realtime-sync.ts
---

# Agent 任务生命周期

从「Issue 被指派给 agent」到「Run 落终态、评论回流」。这是全系统最核心、状态最多的一条链路。

## 触发点

| 触发方式 | 入口 | 前置条件 |
|---|---|---|
| Issue 指派 / 创建时指定 agent / 状态流转 | `UpdateIssue`（`handler/issue.go:3999`，决策点 `:4347`）、`CreateIssue`（`:3361`）、`BatchUpdateIssues`（`:4790`，决策点 `:5122`） | 唯一决策谓词 `WillEnqueueRun`（`issue_trigger.go:103`）为真 |
| 手动重跑 | `RerunIssue` | issue 存在、调用者有权限 |
| 评论 @agent / 指派评论线程 | `comment` 路径 → 同一 enqueue | 线程存在、invoke 权限通过 |
| 唤醒（events / time） | `CreateWakeupTask` | 订阅已注册且被消费 |
| 自动重试 | `CreateRetryTask` | 父任务可重试且未超次数 |
| 自动化（Autopilot） | `DispatchAutopilotForPlan` | 见 [自动化调度链路](自动化调度链路.md) |
| Chat 消息 | `SendDirectChatMessage` | 见 [Chat 会话与流式回复](Chat会话与流式回复.md) |

**`WillEnqueueRun` 的否决条件**（`issue_trigger.go:105-160`）：无 assignee；issue 在 Triage；assign/create 目标是 `backlog` 停放；status 只在 `backlog → 非backlog/done/cancelled` 时触发；agent 已归档或无 runtime；自赋值抑制；status 来源时 `hasPendingRun` 去重；squad 走 leader 的 `AgentReadiness`。

## 数据流路径

```
Issue 写入（事务内）
  → WillEnqueueRun 决策 ──deny──> 返回 DispatchBlocked + reason_code（不写任务）
  → dispatchIssueRun（`handler/issue_trigger.go:85`）
  → EnqueueTaskForIssueWithHandoff（task.go:1232）
     ├─ triage 守卫 / agent 归档或无 runtime 报错 / attribution fail-closed / 项目访问
     └─ CreateAgentTask（task.go:1382）→ agent_task_queue 行（status=queued|deferred）
  → 事务提交 → bus.Publish("task:queued") + daemon 唤醒
  ↓
daemon 轮询/WS 唤醒 → POST /api/daemon/tasks/claim（client.go:306）
  → ClaimTasksByRuntime（handler/daemon.go:2100）
  → ClaimTasksForRuntimes（task.go:4169）→ claimTask（:3680）
  → SQL ClaimAgentTask（agent.sql:770）：候选子查询 `FOR UPDATE SKIP LOCKED`（:829）+ 事务级 advisory 容量锁（`runtime.sql:708`）
  → 行变 dispatched（写 dispatched_at + prepare_lease_expires_at，租约 45s）
  ↓
daemon 准备环境/写 prompt → POST /tasks/{id}/start（daemon.go:4752）
  → StartTaskForClaim → LockAgentTaskStartClaim（代际 CAS：runtime_id + dispatched_at）
  → StartAgentTaskWithSupplement → status=running
  ↓
运行期：POST /tasks/{id}/progress（节流进度）、/messages（500ms 批量流式）、/usage（按 model 覆盖）
  ↓
终态：POST /tasks/{id}/complete 或 /fail
  → CompleteTaskWithTransition / FailTaskWithTransition（单事务）
  → 写 task 终态 + 系统/agent 评论 + chat 指针 + 事件
  → bus.Publish("task:completed" | "task:failed") → WS 扇出到前端
```

## 关键节点详解

| 节点 | 文件位置 | 核心逻辑 | 异常处理 |
|---|---|---|---|
| 决策谓词 | `service/issue_trigger.go:103` | 单一函数决定「该不该跑」，所有入口共用 | 返回 `(bool, reason)`；不写库 |
| 幂等索引 | `migrations/452_agent_task_pending_thread_unique.up.sql:2` | `(issue_id, agent_id, COALESCE(comment_thread_id,0))` 且 `status IN ('queued','dispatched')` | 冲突 → `ErrDuplicatePendingTask` → 返回 `coalesced` |
| 入队事务 | `service/task.go:1281-1400` | 前置门禁 + 插入任务 + 发布事件同事务 | attribution 失败 fail-closed；triage 硬拒 `ErrIssueInTriage` |
| 领取 | `agent.sql:770-830` | `SKIP LOCKED` 取候选；同 runtime 先抢 advisory lock 再读容量 | 无候选返回空；liveness 不满足直接跳过 |
| 存活门槛 | `agent.sql:806-808` | `r.status='online' AND last_seen >= now()-150s` | 超阈值不发放 |
| 准备租约 | `service/task.go:203`（45s）、`:202`（90s 重投窗口） | 领取后必须在租约内 start，否则任务可被重投 | 丢响应靠 `ReclaimStaleDispatchedTasksForRuntimes`（`agent.sql:938`）重投 |
| start CAS | `agent.sql:1002`（`LockAgentTaskStartClaim`） | `FOR UPDATE` + 匹配 `dispatched_at`（纳秒精度） | 不匹配 → 409 `task claim is stale` |
| 完成 | `service/task.go:4712-4903` | 终态（`CompleteAgentTask`）+ `HasAgentCommentedSince` 检查（无则用输出合成评论）+ chat 结果行 | 上下文耗尽改判失败（`daemon.go:4951`） |
| 失败 | `service/task.go:5142-5515` | 分类（`taskfailure.Classify`）→ 建重试子任务 → 系统评论 → 事件 | `ON CONFLICT DO NOTHING` 防重试风暴 |
| 孤儿恢复 | `service/task.go:6304`、`agent.sql:1239`(定义)/`:1246`(UPDATE) | daemon 重启后把遗留 `dispatched/running/waiting` 全判失败（`runtime_recovery`） | 随后走 `HandleFailedTasks` 统一补偿 |
| 清扫 | `cmd/server/runtime_sweeper.go:163-174` | 30s 一轮，6 阶段：stale runtime → offline task → reconnect retry → stale task → queued 过期 → chat 收尾 | 阈值 150s；断连宽限默认 3h |

## 状态机（`agent_task_queue.status`）

```
queued ──claim──> dispatched ──start──> running ──complete──> completed
   │                  │                    └─fail────────┬─> failed
   │                  ├─waiting_local_directory ─start───┘
   │                  └─fail / 超时重投 ───────────────────> failed / queued
   ├─deferred（fire_at 未来或渠道媒体待定）──promote──> queued
   └─cancel（用户/issue 取消/解绑）───────────────────────> cancelled
```

允许值 CHECK：`migrations/128_comment_routing_escalation.up.sql:6-17`。

## 异常路径

| 异常类型 | 处理方式 | 用户可见结果 |
|---|---|---|
| issue 在 Triage | 直接拒绝 `issue_in_triage` | 明确错误，不建任务 |
| agent 无 runtime / 已归档 | 入队时报错 | 错误码 + 提示 |
| runtime 离线 | 不发放任务；超 `MULTICA_RUNTIME_RECONNECT_GRACE`(3h) 后判失败 | 任务失败原因 `runtime_offline` |
| 领取后未 start（租约过期） | 90s 后重投 | 无感；同任务二次领取 |
| 进程残留未确认 | `StartTask` 先 requeue（`daemon.go:4717`）或以 `cleanup_blocked` 失败（`:4731`） | 可能显示失败原因 |
| 终态回调超时（daemon 侧） | daemon 重试；已终态返回 `transitioned=false` 幂等成功 | 无重复副作用 |
| 输出含 NUL | 事务回滚（22P05），任务卡 `running` | **需人工**；新字段必须加进 `sanitizeTaskCompleteRequest` |
| 配额/issue 上限 | `quota_exceeded` / `issue_limit_reached` | 明确拒绝码 |

## 关联组件

- **Service**：`service/task.go`（生命周期核心）、`issue_trigger.go`（决策）、`task_claim_decision.go`（领取裁决）、`task_triage_guard.go`
- **Handler**：`handler/daemon.go`（全部 daemon 端点）、`handler/admission.go`（响应契约）
- **Daemon**：`internal/daemon/{daemon,client,prompt,task_cli}.go`
- **执行后端**：`pkg/agent/*`（按 `SupportedTypes` 分派）
- **类型**：`internal/daemon/types.go:78`（`Task`）、`packages/core/types/agent.ts`
- **前端**：`packages/core/realtime/use-realtime-sync.ts`（`task:queued` `:1840`、`task:running` `:1871`、`task:completed` `:1934`）

## 相关业务流

→ 详见 [实时事件广播链路](实时事件广播链路.md)（任务事件如何到达前端）
→ 详见 [Chat 会话与流式回复](Chat会话与流式回复.md)（chat 任务的差异）
→ 详见 [自动化调度链路](自动化调度链路.md)（Autopilot 如何产生任务）
