---
last_updated: 2026-11-14
source_files:
  - .github/workflows/{ci,release,mobile-verify,desktop-smoke,ui-performance}.yml
  - .github/RELEASING.md
  - .github/PULL_REQUEST_TEMPLATE.md
  - .github/ci-paths.json
  - CONTRIBUTING.md
  - docs/engineering/
---

# Git 工作流与 CI

## 分支与提交

| 项 | 约定 |
|---|---|
| 主分支 | `main`（CI 在 push 与 PR 上均触发） |
| 特性分支 | 自由命名（如 `agent/agent/orio-2`）；不强制前缀 |
| 提交信息 | Conventional commits：`feat(scope)`、`fix(scope)`、`refactor(scope)`、`docs`、`test(scope)`、`chore(scope)`；原子提交按意图分组 |
| 合并方式 | PR（`main` 为目标），走仓库 PR 模板 |

## PR 要求（`.github/PULL_REQUEST_TEMPLATE.md`，缺失即退回）

1. **Problem** —— 具体问题、谁受影响、为什么重要
2. **Goal** —— 改动后应为什么真
3. **Proposal** —— 高层设计，让不了解背景的读者也能懂；实现细节最少化
4. **Alternatives**（可选）
5. **Screenshots** —— **UI 或交互改动必须附截图/录屏**，缺省默认拒绝；无 UI 影响则写 "N/A" 并说明原因

结尾 `Closes #<issue>`。上传用 `scripts/pr-shots`（按 SHA-256 去重，可重试）或 GitHub 原生附件。

## CI 结构

主工作流 `ci.yml` 先用 `changes` 任务做**路径选择**，再按需跑分支任务：

| 任务 | 触发 | 关键内容 |
|---|---|---|
| `changes` | 总是 | 先 `node --test scripts/ci-scope.test.mjs`（路径选择本身必须有测试），再 paths-filter + `decide` |
| `sqlc-check` | sqlc 路径 | `make sqlc` 无 diff、无未跟踪生成物 |
| `frontend-build` / `-test` / `-views-test` / `frontend-quality` | 前端路径 | turbo 缓存 + 分片测试 |
| `backend-tests` / `backend-agent-tests` | 后端路径 | Go 测试分两半（`pkg/agent` 限流） |
| `go-vulnerability-scan` | 后端路径 | `govulncheck`，fail-closed |
| `backend-darwin-vet` | 后端路径 | darwin 编译与 vet（每日另有全量） |
| `windows-execenv` / `macos-runtime` / `macos-processtree` | runtime / processtree 路径 | 平台专属进程与信号回归 |
| `image-budget` / `installer` / `script-checks` | 各自路径 | 镜像体积、安装脚本、shell 脚本测试 |
| 聚合 | — | `backend` / `frontend` 汇总状态 |

其他工作流：`mobile-verify.yml`（仅 lint+typecheck，路径过滤到 mobile 与 core）、`desktop-smoke.yml`、`ui-performance.yml`、`openclaw-config-smoke.yml`。

`ci-paths.json` 定义分组：`frontend`、`backend`、`sqlc`、`runtime`、`processtree`、`quality_only`、`scripts`、`installer`、`images`、`full`。

## 版本与发布（`.github/RELEASING.md`）

- **只有推送 tag 才能发布**：`v*.*.*`（拒绝 `v*-dirty*`）。Release 工作流**没有**手动触发入口。
- tag 名必须严格匹配 `vX.Y.Z` 或 `vX.Y.Z-suffix`。
- 发布链：`verify`（Go 测试 + `govulncheck`，fail-closed）→ `release`（二进制/Homebrew）→ Docker 多架构 build+merge（backend 与 web 各自）→ Helm chart → desktop。
- 版本变更同步点：`apps/web/package.json`、`apps/desktop/package.json`、`packages/views/whats-new/releases.ts`、`apps/web/features/landing/i18n/*.ts`（0.9.0 提交 `8cd8a2db9` 演示了这个五处联动）。
- 默认补丁版本递增，除非另有说明。

**漏洞扫描绕过**：仅当 `govulncheck` 本身或漏洞库不可用，或已确认的误报阻塞紧急发布；在仓库变量 `ALLOW_VULN_BYPASS_FOR_TAG` 写入精确 tag，重跑后**立即删除**。

## 平台与工具链约束

- CI/发布 Go 版本 `~1.27.4`（安全底线），与 `server/go.mod` 的开发最低版本独立；提升时必须同步所有 Go 工作流与 `Dockerfile` 的 builder 镜像。
- Node 22（镜像内精确到 22.23.4 补丁）。
- `turbo.json` 的 `globalDependencies` 包含 `.github/workflows/ci.yml`——否则改 Node 版本会重放旧缓存。

## 生成物必须在库里同步

CI 会重跑生成器并校验 diff（`script-checks`）：

| 生成器 | 产物 |
|---|---|
| `node scripts/generate-reserved-slugs.mjs` | `packages/core/paths/reserved-slugs.ts` |
| `node scripts/generate-attachment-text-preview.mjs` | `packages/views/editor/utils/text-preview-whitelist.generated.ts` |
| `make sqlc` | `server/pkg/db/generated/` |

## 本地环境与仓库约定

- 主 checkout 用 `.env`，worktree 用 `.env.worktree`（**不要**把 `.env` 拷进 worktree）。
- 共享同一个 PostgreSQL 容器（`localhost:5432`），隔离靠不同数据库名。
- 移除 worktree 必须用 `make remove-worktree WORKTREE=...`，直接 `git worktree remove` 会留下孤儿数据库。
- 在 agent 任务目录（存在 `.multica/daemon_task_context.json` 标记）里，CLI 拒绝 `daemon start/stop/status`，防止任务操控你自己的 daemon。

## 相关文档

- 具体测试分层与命令 → [测试策略](测试策略.md)
- 环境与端口 → [部署与运行架构](../01-架构总览/部署与运行架构.md)
- 改动范围与反模式 → [编码规范](编码规范.md)
