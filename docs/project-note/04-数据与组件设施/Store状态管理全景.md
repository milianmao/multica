---
last_updated: 2026-11-14
source_files:
  - packages/core/（57 个 zustand 文件，49 个唯一 useXxxStore 导出）
  - packages/core/platform/workspace-storage.ts
  - packages/core/query-client.ts
  - packages/core/issues/stores/
  - packages/core/chat/store.ts
  - packages/core/auth/store.ts
  - apps/desktop/src/renderer/src/stores/tab-store.ts
---

# Store 状态管理全景

## 两套状态，边界不可越

| 状态类型 | 归属 | 例子 |
|---|---|---|
| 服务端数据 | **TanStack Query** | issue 列表/详情、成员、agent、runtime、chat 消息 |
| 客户端 UI 态 | **Zustand** | 选中项、面板宽度、草稿、筛选条件、tab 布局、主题 |

规则：Zustand **不存服务端数据**，也不缓存 API 响应；Query **不存**「哪个弹窗开着」。

## Store 定义位置规则

| 位置 | 允许 | 强制 |
|---|---|---|
| `packages/core/<domain>/**` | 是（57 个 zustand 文件 / 49 个唯一 store） | Web + Desktop 共享 |
| `packages/views/` | **否** | ESLint 禁 `zustand` 的 `create`/`createStore`（`packages/views/eslint.config.mjs:19-25`） |
| `apps/desktop/src/renderer/src/stores/` | 是（平台态） | tab-store 等 desktop 独有 |
| `apps/mobile/data/stores/` | 是（mobile 独有） | 不复用 web/desktop store |

命名约定：文件 `<feature>-store.ts`，导出 `use<Feature>Store`（`conventions.mdx`）。

## Workspace 身份：所有持久化的前提

`packages/core/platform/workspace-storage.ts` 是「当前是哪个工作区」的**单一真源**：

- `setCurrentWorkspace(slug, wsId)`（`:34`）——slug 变化才触发副作用，且副作用**延迟到 microtask**（React 19 禁止 render 期跨组件 setState）。
- 两个副作用：① 通知订阅者（WSProvider 重连）② 所有注册的 persist store 从新 slug 的命名空间 rehydrate（`:63-75`）。
- `getCurrentSlug()/getCurrentWsId()` 供 API client（`client.ts:934-935`）与 query key 派生使用。

写入点：Web `app/[workspaceSlug]/layout.tsx:60-61`；Desktop `components/workspace-route-layout.tsx:107`（并做 owner 校验）；Mobile 自管（`data/api.ts:237-239`）。

## 主要 Store 清单

### 认证与平台

| Store | 位置 | 内容 |
|---|---|---|
| `useAuthStore` | `auth/store.ts`（经 `auth/index.ts` 的 Proxy 暴露实例） | `user`、`status(authenticating/authenticated/unauthenticated/recovering)`、`expired` 标记、登录/登出/Google/Token 方法 |
| `useNavigationStore` | `navigation/store.ts` | `lastPath`（persist，排除 `/login` `/workspaces/` 等前缀） |
| `useRailOrderStore` | `navigation/rail-order-store.ts` | 左侧 rail 顺序 |
| `useModalStore` | `modals/store.ts` | 全局弹窗注册 |
| `useShortcutStore` | `shortcuts/store.ts` | 快捷键覆盖 |
| `useAppearanceStore` | `appearance/index.ts` | 主题、字体、密度 |
| `useInstalledThemesStore` | `appearance/installed-themes.ts` | 设备上安装的主题（含社区商店来的） |
| `useSessionPromptStore` | `session-prompt/index.ts` | 会话提示 |

### Issue 域（18 个 UI 态 store，最密集）

`issues/store.ts`（`activeIssueId`）+ `issues/stores/`：

| 分组 | Store |
|---|---|
| 选择与视图 | `selection-store`、`view-store`、`issues-scope-store`、`my-issues-view-store`、`actor-issues-view-store` |
| 创建与草稿 | `draft-store`、`quick-create-store`、`create-mode-store`、`issue-create-settings-store` |
| 评论 | `comment-composer-store`、`comment-draft-store`、`comment-collapse-store` |
| 侧栏 | `sidebar-tree-store`、`sidebar-kept-store`、`issue-sidebar-sections-store` |
| 子任务 | `sub-issue-display-store`、`sub-issues-collapse-store` |
| 其它 | `recent-issues-store`、`resolved-expand-store`、`issue-opening-store` |

### Chat / Inbox / 命令面板

| Store | 内容 |
|---|---|
| `chat/store.ts` | 草稿（按 session 分槽，`__new__` 为新会话槽）、草稿附件、已应用的服务端草稿恢复账本、未达 composer 的恢复队列、窗口尺寸/展开态 |
| `chat/recent-context-store.ts` | 最近引用的 issue/项目 |
| `inbox/filter-store.ts`、`inbox/selection-store.ts` | 收件箱筛选与多选 |
| `command-bar/{search,recent,context}-store.ts` | ⌘K 搜索与最近访问 |

### 域视图态（模式统一）

`agents/`、`autopilots/`、`projects/`、`skills/`、`squads/` 各有一个 `stores/view-store.ts`，全部由 **`createCollectionViewStore` 工厂**生成（同构的排序/密度/列配置）。新增集合页应复用该工厂，不要再手写一个。

### 其它

`drafts/create-draft-store.ts`（`createDraftStore<T>` 工厂）、`agents/manual-draft-store`、`projects/draft-store`、`feedback/draft-store`；`github/connection-store`；`runtimes/custom-pricing-store`；`star-prompt/index`；`collections/view-store`；`issue-views/{active-view,edit,rename}-store`；`issue-statuses/archive`。

## Selector 与持久化模式

```ts
// 稳定引用：选择器返回原始值或已存在的对象，不要返回新构造的 {} / []
const count = useIssueSelectionStore((s) => s.ids.length);
// 需要返回新对象时用 shallow
const { a, b } = useFooStore(useShallow((s) => ({ a: s.a, b: s.b })));
```

- 持久化一律经 `createWorkspaceAwareStorage`（`workspace-storage.ts`）→ 命名空间带 workspace slug；**只有** 全局偏好（如 `navigation/store` 的 `lastPath`）用 `defaultStorage`。
- 切换工作区时由 `registerForWorkspaceRehydration` 统一 rehydrate，store 内部不自己监听。
- 直接触达 `localStorage`/`sessionStorage` 在 core 中被 ESLint 拦（`packages/core/eslint.config.mjs:39-50`），白名单只有 `platform/storage.ts`、`analytics/exception-dedupe.ts`、`appearance/safe-mode.ts`。

## Desktop 独有：Tab Store

`apps/desktop/src/renderer/src/stores/tab-store.ts`：persist key `multica_tabs`、`version: 5`；核心动作 `openTab`(:183)、`navigateActiveSession`(:218)、`switchWorkspace`(:176)；选择器 `getActiveTab`(:1770)、`useActiveTabUrl`(:1808)。

**关键约束**：Desktop 的 tab URL 就是 location（renderer 在 `file://`），只有 `platform/tab-coordinator.ts` 能写 router（:19-21）。绕过 adapter 的导航不会报错，只会被判协议错误并静默 reconcile。

## 相关文档

- Query 侧配置与 key 约定 → [服务层与IPC通信](服务层与IPC通信.md)
- 组件如何消费这些 store → [组件架构与通信](组件架构与通信.md)
- 为什么这样切 → [关键设计决策](../02-功能结构和策略/关键设计决策.md) D9
