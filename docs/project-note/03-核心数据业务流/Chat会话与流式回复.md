---
last_updated: 2026-11-14
source_files:
  - server/internal/handler/chat.go
  - server/internal/service/{task,chat_submission,chat_quick_actions,chat_quick_actions_generate}.go
  - server/internal/handler/daemon.go
  - packages/core/chat/message-cache.ts
  - packages/core/realtime/use-realtime-sync.ts
  - packages/core/chat/queries.ts
  - docs/engineering/chat-submissions.md
---

# Chat 会话与流式回复

用户在 Chat 页面与 agent 对话：一次发送如何变成一次 Run、token 如何流回浏览器、取消/重放如何不产生重复。

## 触发点

| 触发方式 | 入口 | 说明 |
|---|---|---|
| 发送消息 | `POST /api/chat/sessions/{sessionId}/messages` → `SendChatMessage`（`handler/chat.go:832`，路由 `router.go:2584`） | 可带 `submission_id`（幂等键）与 `attachment_ids` |
| 取消 | `POST /api/tasks/{id}/cancel` → `CancelTaskByUser`（`chat.go:1825`） | chat 任务仅会话创建者可取消 |
| 重新生成追问建议 | `POST /chat/sessions/{id}/quick-actions/regenerate`（`router.go:2588`） | 只接受「仍是最后一条 assistant turn」 |

**前置条件链**（全部在落库之前，任一失败即不写任何行）：会话归属与可见性（`chat.go:860-874`）→ 提交回执快速命中（`:875-897`）→ 会话未归档（`:906`）→ agent 未归档且有 runtime（`:912-931`）→ invoke 权限（`:945-948`，无 admin 旁路）。

## 数据流路径

```
发送
  → 预检链（见上）
  → 事务开始
     ├─ LockChatSessionForRuntimeBind（行锁，与删除路径互斥）
     ├─ 重读 session/agent（防 TOCTOU）
     ├─ HasPendingChatTurnForSession → 决定是否排队（supports_queue）
     ├─ CreateChatTask → agent_task_queue 行
     ├─ SetChatTaskInputOwnerSelf → user 消息绑定为本 task 的不可变输入批
     └─ CreateChatMessage(role=user)  +  写提交回执（同事务）
  → 提交后通知 daemon（唤醒）
  ↓
daemon 执行 → 每 500ms 批量 POST /tasks/{id}/messages（首条立即 flush）
  → ReportTaskMessages（handler/daemon.go:5791）
     ├─ idempotency_key → uuid.NewSHA1(taskID, key)
     ├─ redact + NUL 清洗
     └─ CreateTaskMessages（一次 SQL 批量，按 seq 排序）
  → bus.Publish("task:message"，带 chat_session_id + seq)
  ↓
前端：session owner 单播 → useRealtimeSync 合并进 ["task-messages", taskId]
  → 按 seq 去重排序（structuralSharing）
  ↓
任务完成 → 写一条 assistant 结果行 → bus.Publish("chat:done")
  → 客户端 inline 插行 + invalidate
  → 异步生成追问建议 → bus.Publish("chat:quick_actions")
```

## 关键节点详解

| 节点 | 文件位置 | 核心逻辑 | 异常处理 |
|---|---|---|---|
| 幂等指纹 | `service/chat_submission.go:52-71` | SHA-256(精确 decoded content + 排序去重后的 attachment ids) | 同 key 异指纹 → 409 `submission_id was already accepted with a different payload` |
| 回执作用域 | `migrations/654_chat_submission_receipt_key.up.sql:1` | `(session, workspace, initiator_user, actor_type, actor_id, submission_id)` 唯一 | 换 UUID 才视为新 turn |
| 事务原子性 | `service/task.go:2408-2608` | 任务 + user 消息 + 回执同事务 | 任一步失败全部回滚，daemon 不会被唤醒 |
| 删除竞态 | `handler/chat.go:686-713` | `LockChatSessionForDelete` 与 enqueue 的 session 行锁互斥 | 防 orphan task（会话删了任务还在跑） |
| 流式写入 | `handler/daemon.go:5856-5920` | 确定性 UUID 幂等 + 批量单语句插入 | 重复批不产生重复行 |
| 客户端合并 | `use-realtime-sync.ts:156,1693-1706` | leading-edge 立即写；其余 100ms 窗口合并 | 按 `seq` 去重排序，乱序到达安全 |
| 排队编辑/移除 | `service/task.go:3247,3302` | channel 输入写 "Stopped."；否则删 user 行 + 归还草稿 | 草稿可恢复（`CreateChatDraftRestore`） |
| 运行中取消 | `service/task.go:3507` | 延后到 daemon `cancel-ack` 结算 | 避免半截输出 |
| 完成 | `service/task.go:4790,4951-5070` | 事务内写 assistant 结果行（函数体 `writeChatCompletionOutcome`） | `writeChatCompletionOutcome` 统一出口 |

## 为什么 chat 内容必须单播

`isChatPrivateEvent`（`cmd/server/listeners.go:78-82`）把**所有 `chat:*`** 与**带 `chat_session_id` 的 `task:message`** 划为私有：只有 session 创建者能收到。

- 任务列表仍工作区可见（成员需要看到 agent 在忙），但内容被清空：`redactForeignChatTaskContent`（`handler/chat_task_privacy.go:10-45`）。
- 缺 `ChatSessionOwnerID` 时**丢弃**而非放宽。

## 异常路径

| 异常类型 | 处理方式 | 用户可见结果 |
|---|---|---|
| 超时后重试同 `submission_id` | 返回首次的 201 与原 `message_id`/`task_id` | 无重复 turn |
| 同 `submission_id` 不同内容 | 409（错误体 `error` 字段可区分） | 提示"该提交已受理过不同内容" |
| 会话已归档 | 预检 400 `chat session is archived`；事务内竞态 409 | 明确错误 |
| invoke 权限被撤销 | 403，**在读取回执之前**返回 | 停止重试；换 UUID 也无法绕过 |
| agent 归档 / runtime 不可用 | 409 | 明确错误 |
| 排队中编辑/移除 | 归还草稿或写 "Stopped." | 无残留半条消息 |
| 追问建议过期 | `ErrChatQuickActionsStale` | 按钮不出现或静默清除 |
| 客户端 pending 标记 | 12s 超时清理 | 不会永久转圈 |

## 关联组件

- **Handler**：`handler/chat.go`、`chat_submission.go`、`chat_history.go`、`chat_title.go`、`chat_task_privacy.go`
- **Service**：`service/task.go`（chat 分支）、`chat_submission.go`、`chat_quick_actions*.go`
- **表**：`chat_message`、`chat_submission_receipt`、`task_message`、`chat_draft_restore`
- **前端**：`packages/core/chat/{queries,message-cache,store}.ts`、`use-realtime-sync.ts`、`packages/views/chat/`
- **契约文档**：`docs/engineering/chat-submissions.md`（权威的提交回执语义）

## 相关业务流

→ 详见 [Agent 任务生命周期](Agent任务生命周期.md)（任务状态与终态处理）
→ 详见 [外部渠道机器人接入](外部渠道机器人接入.md)（渠道消息如何进入同一套 chat 机制）
