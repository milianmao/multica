---
name: query-knowledge
version: 1.2
description: v1.2 - 🆕 predev 解析 lesson frontmatter，透传 status/promoted_at/kb_entry/superseded_by，自动排除 rejected/superseded，历史无 frontmatter lesson 按 legacy 兼容。v1.1 - 🆕 kb-aware 子命令（一次 overview+search + KB 内容 hash 缓存，替代 deep-planning/team 内联重复调用，D2/T3 修复）。 统一检索项目知识背景 — 一站式查询 docs/project-note/ Markdown KB、.codegraph/ 知识图谱（符号级）、pre-dev-checklist 错题本匹配。所有需要"了解项目背景/找代码/查错题"的 Skill 都应通过本 Skill 检索。
evaluated_by:
  - DP-H-001
  - QK-E-001
  - QK-H-001
  - SC-E-003
  - SC-H-006
---

# Skill: query-knowledge — 统一知识背景检索

## Description

**统一入口**，封装三类项目知识背景的渐进式披露检索：

| 后端 | 数据源 | 检索粒度 | 用途 |
|------|--------|---------|------|
| `project-note` | `docs/project-note/` 三层 Markdown KB | 文档 / 章节 / 全文 | 了解架构 / 功能 / 业务流 |
| `codegraph` | `.codegraph/codegraph.db` (SQLite) | 符号 / 函数 / 调用关系 | 找代码 / 改代码影响面 |
| `predev` | `.claude/artifacts/bugs/*/knowledge/` + 全局 feedback | 错题模式匹配 | 规划前的同类经验检索 |
| `all` | 三者聚合 | — | 一站式全景检索 |

**渐进式披露**：默认仅返回索引 + top-N 摘要，需要全文再 `read` 指定路径。**避免一次加载所有 L3 文档浪费 token**。

---

## 调用方式

### CLI 形式（推荐，供其他 Skill 用 Bash 调用）

```bash
SCRIPT="skills/query-knowledge/scripts/query_knowledge.py"  # 仓库相对路径（Agent 在仓库根执行）

# 1. 检索项目知识库
python3 $SCRIPT --repo . --backend project-note search "主题切换" --top 3

# 2. 检索代码符号
python3 $SCRIPT --repo . --backend codegraph cg-search "Handler" --kind function --limit 5
python3 $SCRIPT --repo . --backend codegraph cg-callers "Handler.do_GET"
python3 $SCRIPT --repo . --backend codegraph cg-impact "Handler.do_GET"

# 3. 错题本匹配（变更前必跑）
python3 $SCRIPT --repo . --backend predev pd-scan \
    --layer backend --tech SSE --tech <your-tech> --file <your/file.py>

# 4. 一站式聚合检索
python3 $SCRIPT --repo . --backend all --json search "HTTP" --top 5 --tech HTTP
```

> ✅ **参数顺序自由**：全局参数（`--backend/--json/--top/--layer/--tech/--kind/--limit`）
> 可放子命令前或后，Skill 内部会自动归一化：
> ```bash
> # 两种写法等价
> query_knowledge.py --backend project-note --top 3 search "xxx"
> query_knowledge.py search "xxx" --top 3 --backend project-note
> ```

### JSON 输出（供其他 Skill 程序化消费）

加 `--json` 即可获得结构化输出，每个返回顶层都包含 `_backend` 或路径字段标识来源。

---

## 子命令清单

### [E] 🆕 v1.1 `kb-aware`（D2/T3 修复：统一 KB 感知入口 + 缓存）

`kb-aware` 一次调用完成 `overview + search`，内置 KB 内容 hash 缓存，避免 deep-planning/team 各自内联 overview+search 导致的 4-5 次重复调用（每条链路浪费 ~4-6k tokens）。

**调用**：
```bash
python3 skills/query-knowledge/scripts/query_knowledge.py \
    --repo . --backend project-note --json \
    kb-aware --task-keywords "<任务关键词>" --task-id "$TASK_ID"
```

**参数**：
- `--task-keywords`：任务关键词（用于 search，空则只返回 overview）
- `--task-id`：控制面 TASK_ID（用于缓存路径隔离；无 TASK_ID 时用 `.runtime/kb-cache.json`）

**返回结构**：
```json
{
  "overview": { "kb_root": "...", "kb_source": "auto-detect", "cached": false, ... },
  "search":   { "hit_count": 5, "hits": [...], "cached": false },
  "cache_path": "control-plane-state/session-drafts/TASK-XXX/kb-cache.json",
  "kb_content_hash": "414591b1...",
  "task_id": "TASK-XXX"
}
```

**缓存守卫语义**：
- `overview.cached=true` -> KB 内容未变（基于 path+mtime hash），复用上次结果
- `search.cached=true` -> 同 keywords+context+top，复用上次检索结果
- 缓存路径按 TASK_ID 隔离，避免不同任务互相污染

**调用方替换清单**：
- `deep-planning-rigorous-validation` 前置步骤（原 2 次调 overview+search -> 改 1 次 kb-aware）
- `team-rigorous-delivery` Stage 0（同上）

### [A] `--backend=project-note` (默认)

| 命令 | 说明 | 输出 |
|------|------|------|
| `stats` | KB 统计 | `{total_docs, by_layer, by_category}` |
| `overview` | KB 全貌（含每分类 L2 摘要） | `{kb_root, categories:[...]}` |
| `list` | 列出所有分类 | `{categories}` |
| `hierarchy` | 打印三层结构 | `{hierarchy: {分类: {L2:[], L3:[]}}}` |
| `category <name>` | 某分类下所有文档 | `{category, docs:[...]}` |
| `search <query>` | 按关键词搜索 | `{hit_count, hits:[{score, path, layer, title, matched_keywords, matched_sections, snippet}]}` |
| `read <path>` | 读取指定文档全文 | `{path, content}` |

**search 特殊参数**：
- `--kb-layer L1|L2|L3`：限定 KB 层
- `--top N`：返回前 N 条（L1 自动插入尾部）
- `--context bugfix|feature`：bugfix 时错题本加权 2x
- `--json`：JSON 输出

### [B] `--backend=codegraph`

| 命令 | 说明 | 输出 |
|------|------|------|
| `cg-stats` | DB 统计 | `{nodes, edges, files, db}` |
| `cg-search <query>` | 符号搜索（FTS5） | `{query, results:[{qualified_name, file, line, kind, docstring, signature}]}` |
| `cg-callers <symbol>` | 谁调用了 symbol | `{symbol, callers:[...]}` |
| `cg-callees <symbol>` | symbol 调用了什么 | `{symbol, callees:[...]}` |
| `cg-impact <symbol>` | symbol 的完整影响面 | `{symbol, callers_count, affected_files, callers}` |

**特殊参数**：
- `--kind function|class|method`：cg-search 类型过滤
- `--limit N`：返回上限（默认 20）

### [C] `--backend=predev`

| 命令 | 说明 | 输出 |
|------|------|------|
| `pd-scan` | 按变更范围扫描活跃错题本 | `{matched_rules, knowledge_files, checklist}` |
| `stats` | 错题本规则、文件和状态分布 | `{rules_count, knowledge_files_count, excluded_knowledge_files_count, knowledge_status_counts}` |

**lesson 状态语义（v1.2）**：`knowledge_files[]` 会透传 `status`，已晋升项还会带 `promoted_at` 与 `kb_entry`；`rejected`、`superseded` 默认不参与活跃匹配（可在 `.query-knowledge.yml` 的 `backends.predev.exclude_statuses` 覆盖）；历史 lesson 无 frontmatter 时以 `legacy` 保持兼容。`verified` 仅表示绑定过业务 gate，**不等于**已写入长期 KB；只有 `promoted` 与 `kb_entry` 同时存在才表示受控回灌完成。

**变更范围参数**（可多次）：
- `--layer frontend|backend|both`：变更层
- `--tech vue-router|SSE|JSON|...`：涉及技术
- `--pattern ...`：涉及模式
- `--file <your/file.py>`：涉及文件

### [D] `--backend=all` 聚合

| 命令 | 说明 |
|------|------|
| `search <query>` | 一站式：KB + codegraph + predev 聚合返回 |

**返回结构**：
```json
{
  "query": "HTTP",
  "backends": {
    "project-note": { "hit_count": 17, "hits": [...] },
    "codegraph":    { "hit_count": 5,  "results": [...] },
    "predev":       { "hit_count": 3,  "matched_rules": [...], "checklist": [...] }
  }
}
```

**返回语义**（每个后端独立）：
- `hit_count: N` + `hits/results/...`：可用且有 N 条结果
- `hit_count: 0`：可用但无匹配（空结果）
- `unavailable: "<原因>"`：后端不可用（如 codegraph schema 不匹配、KB 不存在）
- `error: "<异常信息>"`：后端异常（如 DB 读取失败）

**典型 `unavailable` 场景**：
- codegraph schema 版本不匹配 → 提示用户 `rm -rf .codegraph/ && 重启 daemon` 重建
- KB 路径未找到 → 提示用户配置 `kb_path` 或建 `docs/project-note/`

---

## 与其他 Skill 的协作模式

### 1. feat-design-planning-v3 (Phase 0 Context Loading)

**改造前**：Phase 0 全量读 27 个 Markdown 文档（~12k tokens 灌入上下文）

**改造后**：
```bash
# Phase 0.1: 仅获取索引 + 摘要（~500 tokens）
python3 $SCRIPT --repo . --backend project-note overview

# Phase 0.2: 按需加载相关 L3 文档
python3 $SCRIPT --repo . --backend project-note search "<需求关键词>" --top 5 --context feature

# Phase 3: 设计前查错题本
python3 $SCRIPT --repo . --backend predev pd-scan \
    --layer backend --tech SSE --file <your/file.py>
```

### 2. fix-bug-workflow (Step 0.5 同类经验检索)

```bash
# bugfix 场景自动加权错题本
python3 $SCRIPT --repo . --backend project-note search "<错误关键词>" \
    --context bugfix --top 5
```

### 3. code review / 改代码前查影响面

```bash
python3 $SCRIPT --repo . --backend codegraph cg-impact "<函数名>"
```

---

## 渐进式披露的三层语义

| 层级 | 何时用 | Token 量 | 命令 |
|------|--------|---------|------|
| **L1 索引** | Phase 0 / 概览 | ~500 | `overview` / `stats` / `list` |
| **L2 摘要** | 定位分类 | ~150/分类 | `category` / `hierarchy` |
| **L3 详情** | 真要改这块 | ~1000-3000 | `read` / `search --top N + read top hit` |

**禁止**一次性全量 read 所有 L3 文档。本 Skill 的核心价值就是**让调用方按需加载**。

---

## 数据源说明

| 数据源 | 生成方式 | 更新时机 |
|--------|---------|---------|
| `docs/project-note/` | `knowledge-base-builder` Skill 构建 | 项目结构大改后 / 2 周一次 |
| `.codegraph/codegraph.db` | codegraph daemon 自动索引 | 代码变更后秒级更新 |
| `.claude/artifacts/bugs/*/knowledge/` | `fix-bug-workflow` 沉淀 | 每次 bugfix 后 |

**数据源延迟**：codegraph 是秒级实时，KB 是人工构建（可能 1-2 周延迟）。调用方需知晓。

---

## 测试

```bash
python3 skills/query-knowledge/tests/test_query_knowledge.py
```

覆盖 4 后端共 30+ 项端到端测试。

---

*v2.0 — 2026-07-05*
*改造动机：feat-design-planning-v3 Phase 0 全量加载 12k tokens 无效浪费，本 Skill 统一封装为渐进式披露入口*