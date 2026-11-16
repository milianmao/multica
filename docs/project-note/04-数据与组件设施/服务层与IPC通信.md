---
last_updated: 2026-11-14
source_files:
  - server/cmd/server/router.go
  - packages/core/api/client.ts
  - packages/core/api/ws-client.ts
  - packages/core/api/schema.ts
  - apps/desktop/src/preload/index.ts
  - apps/desktop/src/main/index.ts
  - apps/desktop/src/shared/main-renderer-messages.ts
  - server/pkg/protocol/events.go
---

# 服务层与 IPC 通信

## 1. HTTP API 面

**基址**：浏览器同源（`/api`、`/ws`、`/v1`、`/auth` 由 Next rewrites 反代，`apps/web/next.config.ts:55-98`）；或显式 `NEXT_PUBLIC_API_URL`（只给 origin，不给路径）。

**认证头**（`packages/core/api/client.ts:930-946`）：

| 头 | 来源 |
|---|---|
| `Authorization: Bearer <jwt>` | 本地 token 模式 |
| cookie `multica_auth` | 浏览器 cookie 模式（+ `multica_csrf*`） |
| `X-Workspace-Slug` | `getCurrentSlug()`（`:934-935`） |
| `X-CSRF-Token` | 读 cookie，优先 session 绑定值，被拒后降级（`:915-928`） |
| `X-Client-Platform/_Version/_OS` | 注入的 `identity` |

**客户端**：`packages/core/api/client.ts`，6059 行、438 个 `async` 方法，按域分组（`listIssues`、`createComment`、`claimTask`…）。桌面/移动端各有一层薄封装。

## 2. 路由分区（`server/cmd/server/router.go`）

| 分区 | 前缀 | 认证 | 备注 |
|---|---|---|---|
| 健康 | `/health`、`/readyz`、`/healthz`、`/health/realtime` | 无 | `/health/realtime` 需 token 或回环 |
| WebSocket | `/ws` | 首帧 auth 或 cookie | 见 §4 |
| 能力下载 | `/api/attachments/{id}/signed-download`、`/api/avatars/{sig}/*` | URL 签名 | 原生/跨站场景拿不到 header |
| 插件 surface | `/plugin-surfaces/{token}` | 签名 + Host 边界 | 独立源 |
| 认证 | `/auth/*` | 限流 | `send-code`、`verify-code`、`google`、`logout` |
| 公开配置 | `/api/config`、`/api/share-links/{code}` | 无 | |
| Webhook | `/api/webhooks/{autopilots,github,vcs,stripe}`、`/api/github/setup`、`/api/integrations/composio/callback`、`/api/apps/mcp` | 签名/凭证 | |
| Daemon | `/api/daemon/*` | `DaemonAuth`（`mdt_`） | 任务 claim/start/complete/fail、环境、模型探测 |
| Public API | `/v1/*` | `mpi_`/`mpc_` | 插件 Action API |
| 会话 + workspace | 其余 `/api/*` | `Auth` + `RequireWorkspaceMember` | 主体 |

主要 workspace 子资源（`router.go:2117-2700`）：`search-index`、`issues`、`quick-actions`、`properties`、`labels`、`issue-statuses`、`issue-workflows`、`projects`、`squads`、`autopilots`、`pins`、`issue-views`、`comments`、`agents`、`agent-builder/sessions`、`skills`、`dashboard`、`runtimes`、`cloud-runtime`、`chat/sessions`、`apps`、`app-requests`、`inbox`、`notification-preferences`。

## 3. 契约与解析

| 关注点 | 规则 | 位置 |
|---|---|---|
| 响应校验 | 一律 zod + `parseWithFallback`，**禁止 `as T`** | `packages/core/api/schema.ts:38-58`；schema 在 `schemas.ts` |
| 未知枚举 | 每个 `switch` 必须有 `default` | `AGENTS.md` |
| 错误码提取 | `errorCode(err)`、`dispatchReasonCode(err)`、`clientErrorMessage(err)` | `client.ts:692-721` |
| 能力协商 | `X-Client-Capabilities`（如 `chat-draft-restore-v1`）与 `CHAT_DRAFT_RESTORE_CAPABILITY` | `client.ts:785`；服务端 CORS 白名单 `router.go:73-78` |
| 分页游标 | 响应头 `X-Agent-Tasks-Next-Cursor` 等 | `router.go` 的 `corsExposedHeaders` |

## 4. WebSocket

| 关注点 | 事实 |
|---|---|
| 连接 URL | `/ws?workspace_slug=…&client_platform=…&client_version=…&client_os=…`；**token 绝不入 query**（`ws-client.ts:89-106`） |
| 认证 | cookie 模式直接升级；token 模式 `onopen` 发首帧 `{"type":"auth","payload":{"token"}}`，服务端回 `auth_ack`（`realtime/hub.go:798-890`） |
| 事件类型 | `domain:action` 形式；完整清单与两种词汇的区别见 [实时事件广播链路](../03-核心数据业务流/实时事件广播链路.md#两种事件词汇不要混用) |
| 订阅 | 客户端**不能**订阅房间；连接时自动加入 workspace / user / workspace_member（`realtime/membership.go:56-60`） |
| 重连 | 1s→30s 指数退避 + ±20% jitter，无限重试（`ws-client.ts:17-18,190-207`） |
| 重连后 | `onReconnect` → `invalidateWorkspaceScopedQueries`（`use-realtime-sync.ts:2092`） |

## 5. Electron IPC（仅 Desktop）

**桥接面**：`apps/desktop/src/preload/index.ts:475-478` 暴露四个全局对象——`electron`、`desktopAPI`、`daemonAPI`、`updater`（`sandbox: true`，见 `electron.vite.config.ts:13-20`）。

**通道名集中在 `apps/desktop/src/shared/`**（类型与解析同处，可测试）：

| 通道 | 方向 | 用途 |
|---|---|---|
| `shell:openExternal` | R→M invoke | 外部链接（唯一出口） |
| `window:close`、`window:open-issue`、`window:setImmersive`、`window:foreground` | R→M | 窗口控制 |
| `app:get-info`、`runtime-config:get` | R→M sync | 版本与运行时配置 |
| `badge:set` | R→M | Dock/任务栏角标 |
| `daemon:*` | R→M | daemon 生命周期（`main/daemon-manager.ts:1483-1600`） |
| `updater:*` | R→M | 更新检查/下载/安装（`main/updater.ts:310-358`） |
| `auth:session-state` | M→R | 会话状态（`shared/auth-session.ts:1`） |
| `main-renderer:channel-state` | M→R | **通道就绪 ack 协议**（`shared/main-renderer-messages.ts:6,53-153`） |
| `tab:select-by-shortcut`、`workspace:open-path`、`window:new-by-shortcut` | M→R | 快捷键/深链路由 |
| `invite:open`、`issue:open`、`inbox:open`、`settings:open`、`auth:token` | M→R | 深链与首启令牌 |

**就绪握手**：`BrowserWindow.loadURL` 完成 ≠ React effect 已订阅。主进程必须先等 renderer 回 ack，否则深链会丢失（`main-renderer-messages.ts:1-5`）。新增 M→R 消息必须登记到 `MAIN_RENDERER_MESSAGE_CHANNELS`。

## 6. Daemon ↔ 服务端

| 通道 | 端点 | 用途 |
|---|---|---|
| WS | `/api/daemon/ws` | 优先通道：唤醒与 RPC（`daemon/wsrpc.go:315`） |
| HTTP claim | `POST /api/daemon/tasks/claim` | WS 不可时的兜底（`daemon/client.go:306`） |
| 注册/心跳 | `/register`、`/heartbeat`、`/deregister` | runtime 生命周期；热路径只写 Redis（TTL 90s），DB 最多落后 60s |
| 任务回传 | `/tasks/{id}/{start,progress,complete,fail,usage,messages,environments}` | 见 [Agent 任务生命周期](../03-核心数据业务流/Agent任务生命周期.md) |
| 能力请求 | `/runtimes/{id}/{update,models,local-skills}/…/result` | 服务端异步向 daemon 索要数据 |
| 环境命令 | `/environment-commands/claim` 与 `/result` | 起停任务环境 |

## 相关文档

- 实时链路全貌 → [实时事件广播链路](../03-核心数据业务流/实时事件广播链路.md)
- Query key 与缓存 → [Store状态管理全景](Store状态管理全景.md)
- 数据结构 → [类型系统与数据模型](类型系统与数据模型.md)
