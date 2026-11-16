# 04 · 数据与组件设施

理解 Multica「状态放哪、组件怎么连、契约长什么样」。

| 文档 | 一句话摘要 | 适用场景 |
|---|---|---|
| [Store状态管理全景](Store状态管理全景.md) | Query（服务端）vs Zustand（客户端）的分界、57 个 core zustand 文件的清单、workspace 身份与 rehydrate、Desktop tab store | 加状态、排查"切工作区后数据串了" |
| [组件架构与通信](组件架构与通信.md) | App shell 骨架、导航抽象契约、六种通信机制、组件分层与单一写入入口 | 新增页面/组件、跨端适配 |
| [服务层与IPC通信](服务层与IPC通信.md) | API 头与分区、zod 契约、WS 协议、Electron IPC 通道表、daemon 端点 | 调接口、加实时事件、改桌面桥 |
| [类型系统与数据模型](类型系统与数据模型.md) | snake/camel 边界、状态两层模型（键 + 生命周期）、实体关系图、sqlc 生成链路 | 加字段、改枚举、理解数据关系 |

## 三条最容易踩的边界

1. **服务端数据不许进 Zustand**，UI 态不许进 Query → [Store状态管理全景](Store状态管理全景.md)
2. **导航只能走 `NavigationAdapter`**，整页跳转被 ESLint 拦 → [组件架构与通信](组件架构与通信.md)
3. **响应必须过 zod + `parseWithFallback`**，禁止 `as T` → [服务层与IPC通信](服务层与IPC通信.md)

## 相关文档

- 这些设施的运行期行为 → [03-核心数据业务流](../03-核心数据业务流/README.md)
- 编码时的具体写法 → [05-项目开发规范](../05-项目开发规范/README.md)
