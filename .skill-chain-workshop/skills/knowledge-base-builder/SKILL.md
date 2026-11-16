---
name: knowledge-base-builder
version: 1.3.1
description: "v1.3.1 — 受控 lesson 晋升边界：日常 Bug 不重建 KB；仅 fix-bug Step 9.2 的已脱敏、已验证 lesson 经用户确认定点回灌 06 摘要，业务流 03 仍须新 run 重验。v1.3 - 🆕 claude-mem 集成（Phase 0 跨 session 记忆检索）。Use when setting up a new project, onboarding to an existing codebase, or the project structure has changed significantly. Builds a multi-layer indexed knowledge base at docs/project-note/ that all downstream skills depend on. Phase 1A uses codegraph MCP for structural indexing; Phase 1B uses understand-anything plugin for semantic analysis; Phase 1+ combines both preprocessors' outputs with manual scanning."
evaluated_by:
  - KB-E-001
  - KB-M-001
  - SC-E-006
---

# Knowledge Base Builder — 项目知识库构建

## Overview

从项目代码库中学习并构建**多层索引、渐进式披露**的结构化知识库。本 Skill 是整个 Skill 链的**第一层基础设施**——所有后续 Skill（feat-design-planning → deep-planning-rigorous-validation → fix-bug-workflow）都依赖知识库来理解项目架构、避免重复踩坑。

**核心设计理念**：
- **双预处理优先** — 在主扫描前先跑 codegraph（MCP 结构图谱）+ understand-anything（plugin 语义图谱），主扫描阶段消费两者结果，避免重复劳动
- **多层索引** — README → 分类索引 → 详情文档，三层渐进，按需深入
- **渐进式披露** — Token 友好，每层只暴露摘要，需要时才展开细节
- **细粒度业务流** — 每条核心业务流程独立成文，方便精准查阅
- **零重复** — 信息只存一处，跨文档通过 `→ 详见 [xxx]()` 引用
- **版本感知** — 每次更新记录变更时间，下游 Skill 可检测知识是否过期
- **🆕 v1.3 claude-mem 集成** — 如果已安装 claude-mem MCP server，在 Phase 0 检测知识库后调用其检索跨 session 记忆，提取已知问题合并到约束清单。未安装则跳过。

**两个预处理器的职责划分**：
- **codegraph**（结构层）— 提供符号/文件/调用边的 SQLite 知识图谱，毫秒级查询，适合回答"X 在哪、谁调了 X、改 X 影响什么"
- **understand-anything**（语义层）— 通过多 agent 体系（project-scanner / file-analyzer / architecture-analyzer / domain-analyzer）做架构分层、领域知识、图谱质量验证，适合回答"项目是干什么的、业务流是什么、设计模式是什么"

## When to Use

| 场景 | 行为 |
|------|------|
| **新项目初始化** | 完整构建 6 大分类知识库 |
| **首次 onboard 项目** | 完整构建（est. 10-15 min） |
| **重大架构变更** | 增量更新受影响分类（`--incremental`） |
| **新增核心业务流** | 追加 `03-核心数据业务流/[新流程].md` |
| **feat-design-planning Phase 0 检测到缺失** | 自动触发构建 |
| **定期维护（每 2-4 周）** | `--validate` 验证一致性，标记过期条目 |

**NOT for:** 小范围代码修改、日常 bug 修复（知识库作为只读参考；仅当 `fix-bug-workflow` Step 9.2 的已脱敏、已验证 lesson 经用户确认晋升时，定点更新 `06-迭代与变更/已知问题与陷阱.md`，不触发全量构建）。

## 输出目录结构

所有产出物写入项目根目录的 `docs/project-note/`：

```
docs/project-note/
├── README.md                          # 🔍 总索引 — 快速定位入口
│
├── 01-架构总览/
│   ├── README.md                      # 架构分类索引
│   ├── 系统分层架构.md                  # 前后端分层、模块边界、依赖方向
│   ├── 技术栈与中间件.md                # 运行时、数据库、消息队列、第三方服务
│   └── 部署与运行架构.md                # 构建流程、环境配置、端口分配
│
├── 02-功能结构和策略/
│   ├── README.md                      # 功能分类索引
│   ├── 核心功能模块.md                  # 功能模块清单、职责边界、模块间交互
│   ├── 设计模式与策略.md                # 项目中实际使用的设计模式、降级策略、缓存策略
│   └── 关键设计决策.md                  # 历史关键决策、决策背景、trade-off 记录
│
├── 03-核心数据业务流/
│   ├── README.md                      # 业务流分类索引（⭐ 最重要 — 细粒度核心）
│   ├── [业务流程1].md                  # 每条核心流程独立成文
│   ├── [业务流程2].md                  # 格式：触发点 → 数据流路径 → 关键节点 → 异常处理
│   └── ...                            # 按业务领域命名（如 会话管理全链路.md）
│
│   # covered 判据（scenario-oracle-builder 机械判定）：触发点 + 数据流路径 + 异常处理
│   # 三项齐全即视为 covered；边界/GWT 属增强字段，缺失不降级。边界场景（降级/超时/
│   # 冲突/不可用/不可变状态等）可写入「异常处理」段落，parser 会识别进 boundary_cases。
│
├── 04-数据与组件设施/
│   ├── README.md                      # 组件设施分类索引
│   ├── Store状态管理全景.md             # 所有 Zustand Store、State 结构、Selector 模式
│   ├── 组件架构与通信.md                # 组件树、Props 流向、事件总线、Context 使用
│   ├── 服务层与IPC通信.md               # 后端服务、IPC 通道、WebSocket 连接管理
│   └── 类型系统与数据模型.md             # 核心 Interface/Type、枚举、数据模型 ER 图
│
├── 05-项目开发规范/
│   ├── README.md                      # 规范分类索引
│   ├── 编码规范.md                      # 命名约定、代码风格、注释规范、文件组织
│   ├── 测试策略.md                      # 测试框架、覆盖率要求、Mock 策略、E2E 规范
│   └── Git工作流与CI.md                 # 分支策略、Commit 规范、CI/CD 流程
│
└── 06-迭代与变更/
    ├── README.md                      # 变更分类索引
    ├── 版本历史.md                      # 版本号、发布日期、关键变更摘要
    ├── 已知问题与陷阱.md                 # 历史 Bug 模式、常见陷阱、规避方案（→ 联动 memory/）
    └── 变更日志.md                      # 每次知识库更新的详细记录
```

## 多层索引策略

### 三层索引架构

```
L1: docs/project-note/README.md
    └── 6 大分类概览 + "我想做 X → 去哪里找" 快速导航表

L2: docs/project-note/0X-分类名/README.md
    └── 本分类全部文档索引 + 每篇一句话摘要 + 适用场景标注

L3: docs/project-note/0X-分类名/[具体文档].md
    └── 完整知识细节（≤300 行/篇，超出则拆分）
```

### Token 效率设计

| 层级 | 读取成本 | 内容密度 | 使用场景 |
|------|---------|---------|---------|
| L1 总索引 | ~200 tokens | 极高（全貌） | AI 首次理解项目 |
| L2 分类索引 | ~150 tokens/篇 | 高（摘要） | 定位到具体文档 |
| L3 详情文档 | ~1000-3000 tokens/篇 | 实操细节 | 具体编码/设计时 |

**关键规则**：AI 按需逐层深入，不一次性读取所有 L3 文档。L1→L2→L3 的渐进式加载可节省 70%+ Token。

### "我想做 X" 快速导航表（L1 README 必须包含）

| 我想做什么 | 去哪里看 |
|-----------|---------|
| 理解项目整体架构 | `01-架构总览/系统分层架构.md` |
| 新增一个功能模块 | `02-功能结构和策略/核心功能模块.md` → `05-项目开发规范/编码规范.md` |
| 修改会话管理流程 | `03-核心数据业务流/会话管理全链路.md` |
| 新增/修改 Zustand Store | `04-数据与组件设施/Store状态管理全景.md` |
| 调用后端 API | `04-数据与组件设施/服务层与IPC通信.md` |
| 写测试 | `05-项目开发规范/测试策略.md` |
| 排查历史类似 Bug | `06-迭代与变更/已知问题与陷阱.md` + `memory/` |
| 了解上次架构变更 | `06-迭代与变更/变更日志.md` |

## Process

### Phase 0: 环境检测（自动）

```
检查 docs/project-note/ 是否存在 + README.md 是否 > 0 字节
  ├── 不存在 → 完整构建模式 (Phase 1-6)
  ├── 存在但 > 14 天未更新 → 询问用户是否增量刷新
  └── 存在且新鲜 → 验证模式 (Phase V)
```

**输出**：构建模式声明（full / incremental / validate）

### Phase 1A: Codegraph 预处理（自动 — 首次/缓存失效时）

**目标**：用 codegraph MCP 索引项目所有符号/文件/调用边，输出**结构层**知识图谱。

**前置检查**：
```bash
# 查看 codegraph 状态
codegraph_status()
# 若未索引 → 触发自动索引（~1s/文件）
# 若已索引 → 直接查询
```

**步骤**：

1. **整体架构概览**（用一次 explore 拿到全貌）：
   ```
   codegraph_explore(question="项目的整体架构是怎么组织的？主要有哪些模块和分层？")
   ```
   重点提取：模块清单、目录结构、入口文件。

2. **关键模块定位**（按需）：
   ```
   codegraph_search("核心模块名")
   codegraph_files() → 列出所有索引文件
   ```

3. **调用关系追踪**（用于业务流分析）：
   ```
   codegraph_callers(target="入口函数")
   codegraph_callees(target="核心 API")
   codegraph_impact(target="被改动的核心类")
   ```

4. **复杂度统计**（用于优先级排序）：
   - 文件大小 / 函数行数 / 扇入扇出

**输出**：
- `.codegraph/` 目录（SQLite 知识图谱 + 索引元数据）
- 内存中的结构化对象：模块清单、调用图、依赖矩阵

**🚪 Gate**：codegraph_status 返回"已索引"且总文件数 > 0。

**失败处理**：若 MCP 不可用，Phase 1A 标记为 `degraded`，Phase 1 退化为"仅手动 + 1B"。

---

### Phase 1B: Understand-Anything 预处理（自动 — 首次/缓存失效时）

**目标**：用 understand-anything 多 agent 体系做**语义层**分析，输出架构/领域/知识图谱。

**步骤**：

1. **项目扫描**（入口）：
   ```
   Agent: understand-anything:project-scanner
   输入: 项目根目录
   输出: project-inventory.json（语言/框架/文件清单/复杂度估计）
   ```

2. **架构分析**（识别分层）：
   ```
   Agent: understand-anything:architecture-analyzer
   输入: project-inventory.json
   输出: architecture-graph.json（每文件所属架构层、依赖方向）
   ```

3. **文件批量分析**（分批跑，每批 ~20 文件）：
   ```
   Agent: understand-anything:file-analyzer
   输入: 文件路径列表（分批）
   输出: 每个文件的 nodes/edges（函数/类/导入/调用）
   ```

4. **领域分析**（业务侧）：
   ```
   Agent: understand-anything:domain-analyzer
   输入: 完整知识图谱
   输出: domain-graph.json（业务领域、业务流、流程节点）
   ```

5. **图谱质量验证**（兜底）：
   ```
   Agent: understand-anything:graph-reviewer
   输入: 合并后的图谱
   输出: 验证报告（缺失节点/边、跨批断层）
   ```

6. **可选增强**（按需）：
   - `understand-anything:article-analyzer` — 分析 `docs/` 下的 markdown
   - `understand-anything:assemble-reviewer` — 语义级断批合并
   - `understand-anything:tour-builder` — 生成引导式学习路径

**输出**：
- `.understand-anything/` 目录（含 knowledge-graph / domain-graph / architecture-graph）
- 内存中的语义对象：架构分层图、业务领域模型、关键设计模式

**🚪 Gate**：至少完成 project-scanner + architecture-analyzer + file-analyzer + graph-reviewer。

**失败处理**：若任一 agent 失败，记录到 `06-迭代与变更/已知问题与陷阱.md` 并继续（部分图谱仍可用）。

---

### Phase 1: 综合扫描（自动 — 消费 1A + 1B 结果）

**目标**：基于 Phase 1A（结构）+ Phase 1B（语义）+ 手动补充，产出 Phase 2-6 所需的全量输入。

**输入**：
- **Phase 1A 输出**：模块清单、调用图、依赖矩阵、复杂度统计
- **Phase 1B 输出**：架构分层、业务领域、设计模式、图谱质量报告
- **手动补充**：项目元信息（CLAUDE.md / package.json / Git 历史）

**步骤**：

1. **读取项目元信息**（手动，1A/1B 不覆盖）：
   - `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` — 项目级 AI 指南
   - `package.json` — 依赖、脚本、项目元信息
   - `tsconfig.json` / `vite.config.ts` — 编译和构建配置
   - `git log --oneline -50` — 最近 50 次提交（提取变更意图）

2. **消费 codegraph 结果**（自动）：
   - 模块依赖矩阵 → 用于 `01-架构总览/系统分层架构.md`
   - 核心函数调用图 → 用于 `03-核心数据业务流/` 各流程
   - 文件复杂度排序 → 决定 L3 文档拆分粒度

3. **消费 understand-anything 结果**（自动）：
   - 架构分层图 → 用于 `01-架构总览/系统分层架构.md`
   - 业务领域模型 → 用于 `02-功能结构和策略/核心功能模块.md`
   - 设计模式清单 → 用于 `02-功能结构和策略/设计模式与策略.md`
   - 关键决策记录 → 用于 `02-功能结构和策略/关键设计决策.md`

4. **追踪核心业务流**（手动 + codegraph 辅助）：
   - 从 understand-anything 的 domain-graph 提取业务流候选清单
   - 用 `codegraph_callers` 验证数据流路径
   - 标注关键节点（状态变更 / 数据持久化 / 外部 API 调用）
   - 记录异常处理路径（降级 / 重试 / 超时 / 回滚）

5. **交叉验证**（双源比对）：
   - 同一概念在 1A 和 1B 的描述是否一致？
   - 不一致 → 以 1B（语义层）为准，1A（结构层）做事实校验
   - 完全缺失 → 标记为"待人工补全"放进 L3 文档

**🚪 Gate**：
- 3 个数据源（手动 + 1A + 1B）都贡献了至少 1 条信息
- 识别 ≥3 条核心业务流程
- 至少 1 个 L3 文档能在不读源码的情况下基于 1A+1B 产出

**降级策略**：
- 若 1A 失败但 1B 成功 → Phase 1 退化为"手动 + 1B 语义"
- 若 1B 失败但 1A 成功 → Phase 1 退化为"手动 + 1A 结构 + 增强手动扫描"
- 若 1A + 1B 都失败 → Phase 1 退化为"纯手动扫描"（回退到原始行为，gates 全部生效）

### Phase 2: 构建 01-架构总览

**输入**：Phase 1 扫描结果
**输出**：3 篇 L3 文档 + 1 篇 L2 索引

| 文档 | 内容要点 | 来源 |
|------|---------|------|
| `系统分层架构.md` | 前后端分层、模块边界、依赖方向图、关键目录说明 | 目录结构 + package.json |
| `技术栈与中间件.md` | 运行时、框架、数据库、第三方服务、版本号 | package.json + 代码 |
| `部署与运行架构.md` | 构建流程、环境变量、端口、启动命令 | scripts + config |

**🚪 Gate**：3 篇文档全部写入，每篇 ≥50 行且无 TODO 占位符。

### Phase 3: 构建 02-功能结构和策略

**输入**：Phase 1 扫描结果
**输出**：3 篇 L3 文档 + 1 篇 L2 索引

| 文档 | 内容要点 |
|------|---------|
| `核心功能模块.md` | 所有功能模块清单、职责边界、模块间交互矩阵 |
| `设计模式与策略.md` | 项目中使用的设计模式、降级策略、缓存策略、错误处理策略 |
| `关键设计决策.md` | 历史关键决策（含决策背景、考虑过的替代方案、trade-off） |

**🚪 Gate**：3 篇文档全部写入。

### Phase 4: 构建 03-核心数据业务流（⭐ 最重要）

**输入**：Phase 1 识别的业务流程
**输出**：N 篇 L3 文档（每条核心流程一篇）+ 1 篇 L2 索引

**每条业务流程文档必须包含**：
```markdown
# [业务流名称]

## 触发点
- 触发方式（UI 点击 / IPC 调用 / WS 消息 / 定时器）
- 触发条件（前置状态要求）

## 数据流路径
触发点 → [节点1: 输入验证] → [节点2: 状态转换] → [节点3: API/服务调用]
      → [节点4: 数据处理] → [节点5: 持久化] → [节点6: UI 更新]
      → 终点

## 关键节点详解
| 节点 | 文件位置 | 核心逻辑 | 异常处理 |
|------|---------|---------|---------|
| ... | src/xxx.ts:45 | ... | ... |

## 异常路径
- 异常类型1: 处理方式 → 降级策略
- 异常类型2: 处理方式 → 用户提示

## 关联组件
- Store: xxxStore
- 组件: xxxComponent
- 服务: xxxService
- 类型: xxxType

## 相关业务流
→ 详见 [相关流程名](../03-核心数据业务流/[相关流程].md)
```

**命名规范**：以业务领域命名，如 `会话管理全链路.md`、`WebSocket连接管理.md`、`Agent编排执行.md`

**🚪 Gate**：≥3 条核心业务流程已文档化，每条包含完整的触发→处理→异常路径。

### Phase 5: 构建 04-数据与组件设施

**输入**：Phase 1 扫描结果
**输出**：4 篇 L3 文档 + 1 篇 L2 索引

| 文档 | 内容要点 |
|------|---------|
| `Store状态管理全景.md` | 所有 Store 清单、State 结构、关键 Selector、更新模式 |
| `组件架构与通信.md` | 组件树、Props 流向、事件通信、Context 使用 |
| `服务层与IPC通信.md` | 后端 API 清单、IPC 通道表、WebSocket 事件类型 |
| `类型系统与数据模型.md` | 核心 Interface/Type、枚举定义、数据模型关系 |

**🚪 Gate**：4 篇文档全部写入，Store/API 清单完整无遗漏。

### Phase 6: 构建 05-项目开发规范 + 06-迭代与变更

**输入**：CLAUDE.md + 代码风格分析 + Git 历史
**输出**：6 篇 L3 文档 + 2 篇 L2 索引

| 分类 | 文档 | 内容要点 |
|------|------|---------|
| 05 | `编码规范.md` | 命名约定、代码风格、注释规范（从 CLAUDE.md + 实际代码提取） |
| 05 | `测试策略.md` | 测试框架、覆盖率、Mock 策略 |
| 05 | `Git工作流与CI.md` | 分支策略、Commit 规范、CI 流程 |
| 06 | `版本历史.md` | 版本号 + 日期 + 关键变更摘要 |
| 06 | `已知问题与陷阱.md` | 历史 Bug 模式、常见陷阱（→ 与 `memory/` 双向链接） |
| 06 | `变更日志.md` | 知识库每次更新的时间戳 + 变更内容 |

**🚪 Gate**：6 篇文档全部写入，`已知问题与陷阱.md` 至少包含 2 条条目（来源：`memory/` + Git 历史）。

### Phase 7: 构建 L1 总索引 + 最终验证

1. **生成 `docs/project-note/README.md`**（L1 总索引）：
   - 6 大分类概览（每分类一句话）
   - "我想做 X → 去哪里找" 快速导航表
   - 知识库最后更新时间和版本
   - 给 AI 的阅读指南（"按需逐层深入，不一次性加载所有 L3"）

2. **零重复检查**：
   - 全文搜索每个关键概念是否只在唯一位置出现
   - 重复内容 → 保留最权威位置，其余替换为 `→ 详见 [xxx]()`
   - 交叉引用完整性检查（所有 `→ 详见` 链接目标文件存在）

3. **生成版本标记**：
   ```
      构建时间: YYYY-MM-DD HH:MM
   项目版本: vX.X.X (from package.json)
   下次建议更新: YYYY-MM-DD (构建时间 + 14 天)
   ```

**🚪 Gate**：全部 6 个分类索引 + L1 总索引存在，零重复检查通过，版本标记已写入。

## 执行模式

### 完整构建模式（默认）

```bash
# 触发：docs/project-note/ 不存在或为空
用户: "初始化项目知识库" / "构建知识库"
  → Phase 0 检测 → full 模式
  → Phase 1A: Codegraph 预处理（首次 → 触发索引；缓存命中 → 跳过）
  → Phase 1B: Understand-Anything 预处理（首次 → 跑全套 agents；缓存命中 → 增量更新）
  → Phase 1: 综合扫描（消费 1A + 1B + 手动补充）
  → Phase 2-6 逐分类构建
  → Phase 7 总索引 + 验证
  → 输出完整知识库
```

**完整模式预估时间**：
- 首次运行（无缓存）：10-20 min（1A 索引 1-2 min + 1B 多 agent 5-10 min + 人工 5-8 min）
- 增量复用（有缓存）：5-10 min（1A 直接查询 + 1B 增量 + 人工 3-5 min）

### 增量更新模式

```bash
# 触发：用户指定更新范围
用户: "更新知识库 --03-核心数据业务流" / "知识库增量更新 --services"
  → Phase 0 检测 → incremental 模式
  → Phase 1A: 检查 codegraph 增量（基于 mtime，仅重新索引变更文件，~秒级）
  → Phase 1B: 调用 understand-anything:file-analyzer 处理受影响的文件
  → Phase 1: 仅对受影响分类消费预处理结果
  → 仅重建指定分类 + 更新 L1/L2 索引 + 记录变更到 06-迭代与变更/变更日志.md
```

**增量模式策略**：
- **范围明确**（如 `--03-核心数据业务流`）→ 仅对相关文件跑 1A + 1B 增量
- **范围模糊**（如 `--services`）→ 全文跑 1A 增量 + 1B 对相关子树重分析
- **force 全量**：用 `--force` 跳过增量，直接重跑完整 1A + 1B

### 验证模式

```bash
# 触发：feat-design-planning Phase 0 或用户主动
用户: "验证知识库" / "检查知识库是否过期"
  → Phase V: 抽样验证
  → 对比代码现状 vs 文档内容
  → 输出一致性报告（标记过期/准确条目）
  → 如果 ≥30% 条目过期 → 建议完整重建
```

## 版本管理策略

### 知识库版本号

独立于项目版本，格式：`KB-v<major>.<minor>`

- **Major bump**：知识库结构变更（新增/合并/重命名分类）
- **Minor bump**：内容更新（新增文档、修正错误、更新描述）

### 过期检测

每篇 L3 文档包含 frontmatter：
```yaml
---
last_updated: 2026-05-19
source_files: [src/stores/xxxStore.ts, src/components/xxx.tsx]
related_memory: [session_closed_immediately_bug]
---
```

下游 Skill 可交叉验证 `source_files` 的 Git 修改时间 vs `last_updated` 来判断是否过期。

### 变更记录

每次知识库更新**必须**追加记录到 `06-迭代与变更/变更日志.md`：
```markdown
## [KB-v1.2] - 2026-05-19
### 新增
- 03-核心数据业务流/Agent编排执行.md
### 更新
- 04-数据与组件设施/Store状态管理全景.md (新增 2 个 v3 Store)
### 删除
- (无)
### 更新人/触发原因
- AI 检测到 Agent 相关 Store 未文档化
```

## 与下游 Skill 的集成

```
Phase 1A: Codegraph            ┐
  (MCP 结构图谱)                │  预处理层 — 为 Phase 1 提供结构化输入
Phase 1B: Understand-Anything  ┘
        │
        ▼
knowledge-base-builder (本 Skill)
   Phase 0 → 1A → 1B → 1 → 2-6 → 7
        │
        │ 产出 docs/project-note/ + .codegraph/ + .understand-anything/
        ▼
feat-design-planning
        │  Phase 0 读取知识库 → 建立约束清单
        │  Phase 2 设计时对照约束清单
        ▼
deep-planning-rigorous-validation
        │  前置步骤 读取知识库 → 理解架构
        │  阶段2 影响分析时对照知识库清单
        │  阶段8b 覆盖度审计时对照需求条目
        ▼
fix-bug-workflow
        │  Step 1 根因分析时查阅 06-迭代与变更/已知问题与陷阱.md
        │  Step 1.5 (新增) 调用 codegraph_impact 评估 Bug 影响面
        │  Step 9 经验沉淀时 → lesson.md 即时检索 + Step 9.2 经确认定点晋升 06 摘要
```

### 自动触发规则

| 触发条件 | 行为 |
|---------|------|
| `feat-design-planning` Phase 0 检测到 `docs/project-note/` 不存在 | **自动调用本 Skill** 构建知识库，完成后继续 Phase 0 |
| `feat-design-planning` Phase 0 检测到知识库 > 14 天未更新 | 警告用户建议刷新，不阻塞流程 |
| `deep-planning` 前置步骤检测到知识库存在 | 正常读取，按需深入 L2/L3 |
| `fix-bug-workflow` Step 1 | 调用 `codegraph_impact(target=buggy_symbol)` 评估影响范围 |
| `fix-bug-workflow` Step 9.2 的 verified lesson | 用户显式确认后定点晋升 `06-迭代与变更/已知问题与陷阱.md` + `变更日志.md`；不运行全量构建；涉及 `03` 业务流时必须用新 run id 重跑业务质量 gate |
| 本 Skill Phase 1A 索引后 | 通知 `codegraph_status()` 可用，feat-design-planning 可用 |
| 本 Skill Phase 1B 图谱生成后 | 通知 `understand-anything:understand-chat` 可用 |

## 零重复原则

### 信息存放唯一性规则

| 信息类型 | 唯一存放位置 | 其他位置引用方式 |
|---------|-------------|----------------|
| Store 定义 | `04/Store状态管理全景.md` | `→ 详见 [Store状态管理全景](../04-数据与组件设施/Store状态管理全景.md#xxxStore)` |
| 业务流数据路径 | `03/[对应流程].md` | 同上模式 |
| 编码规范 | `05/编码规范.md` | 同上模式 |
| 历史 Bug 模式 | `06/已知问题与陷阱.md` (摘要) + `memory/` (详情) | `→ 详见 memory/xxx.md` |
| API 契约 | `04/服务层与IPC通信.md` (清单) | `→ 详见 [服务层与IPC通信](../04-数据与组件设施/服务层与IPC通信.md#xxx-api)` |

### 检查清单（Phase 7 执行）

- [ ] 每个关键概念在知识库中只出现一次（完整定义）
- [ ] 所有交叉引用使用 `→ 详见 [文档名](../分类/文档名.md#锚点)` 格式
- [ ] 所有 `→ 详见` 链接的目标文件实际存在
- [ ] L1 和 L2 索引不含详情，只含摘要 + 链接
- [ ] 无两篇 L3 文档包含相同的事实性陈述

## Red Flags

- 知识库文档中包含大段复制粘贴的代码 → 应引用源文件路径 + 行号，而非复制代码
- L3 文档 > 300 行 → 应拆分为子文档
- L2 索引 > 50 行 → 摘要不够精炼，应进一步压缩
- 两个文档描述同一概念 → 重复，选择一个作为权威来源
- 知识库 > 30 天未更新且项目有重大变更 → 风险极高，应尽快刷新
- "这个项目太简单不需要知识库" → 即使简单项目也有隐性约定，缺少知识库会导致 AI 做出错误假设

## Related Skills

| Skill | 关系 |
|-------|------|
| `feat-design-planning` | 直接下游：Phase 0 依赖本 Skill 产出 |
| `deep-planning-rigorous-validation` | 间接下游：前置步骤读取知识库 |
| `fix-bug-workflow` | 间接下游：Step 1 查知识库避坑，Step 9 建议更新知识库 |
| `superpowers:brainstorming` | 本 Skill Phase 1 可用 brainstorming 框架做需求澄清 |
| `init-architect` | 互补：init-architect 做项目初始化索引，本 Skill 做深度知识沉淀 |
| **预处理依赖** | |
| `codegraph` (MCP) | **Phase 1A 强依赖**：提供结构层知识图谱，毫秒级查询 |
| `understand-anything:*` (plugin) | **Phase 1B 强依赖**：提供语义层多 agent 分析 |
| `dispatching-parallel-agents` | 优化：Phase 1B 多个 agent 可并行跑（file-analyzer 分批 + domain-analyzer + graph-reviewer） |
