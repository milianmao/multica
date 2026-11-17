---
name: sensitive-info-filter
version: 1.2
description: Use before writing source code or Markdown to docs/project-note/ or any AI-shared knowledge base. Filters API keys, tokens, private keys, PII (phone/ID/email), bank cards, internal IPs, and business-sensitive terms using a 4-layer defense (regex → entropy → Luhn → wordlist). Triggers on "敏感信息过滤", "脱敏", "redact secrets", "scan for credentials", or as Phase 0.5 of knowledge-base-builder. Maintains audit log (hash only, no plaintext) and pauses for user confirmation on high-severity hits. v1.2 additionally requires a second scan before fix-bug-workflow Step 9.2 promotes lesson content into docs/project-note/.
evaluated_by:
  - FB-M-002
  - SC-A-001
  - SC-E-004
  - SC-M-004
---

# Sensitive Info Filter — 敏感信息过滤 (v1.1)

## Overview

A **4-layer defense** filter that prevents sensitive data (API keys, tokens, PII, bank cards, internal infrastructure) from leaking into AI-shared knowledge bases (`docs/project-note/`, `memory/`, `.claude/artifacts/bugs/`, etc.). The filter is **pre-write** (源头控制): sensitive data is sanitized *before* it ever reaches any downstream Skill's context window.

**Why pre-write, not post-read?** Once a secret lands in the LLM context window, it's already too late — it may be cached, logged, or echoed back. Pre-write filtering is the only way to guarantee no leakage.

**Core design principles:**
- **4-layer defense** — No single layer is 100% accurate; layers compound to catch ~99%
- **Replace, don't delete** — `<REDACTED:类型>` preserves structure (LLM still knows "there's a phone field here")
- **Severity-graded** — `high` blocks + asks user; `medium`/`low` auto-redact
- **Audit without exposure** — Logs only file path + position + hash, never the original text
- **Zero external deps** — Pure Python stdlib (`re`, `math`, `hashlib`, `pathlib`)

## When to Use

| 场景 | 行为 |
|------|------|
| **knowledge-base-builder Phase 0.5** | **自动调用**：扫描前先净化源文件 |
| **手动脱敏单文件** | `python -m filters.sensitive_filter path/to/file.md` |
| **CI/CD 提交前钩子** | `pre-commit` hook：扫描 staged 文件 |
| **定期审计** | `cron` 每周扫一次 `docs/project-note/` 防回填 |
| **新项目初始化** | 第一次构建知识库前必跑 |
| **Bug 修复后沉淀** | `fix-bug-workflow` Step 9.0 写 lesson 前，以及 Step 9.2 受控晋升长期 KB 前 |

**NOT for:** Runtime data sanitization in production (use a dedicated DLP tool), encrypting data at rest, or compliance reporting (use a SIEM/GRC tool).

## The 4-Layer Defense

```
源代码 / Markdown
   ↓
┌──────────────────────────────────────────────────────────┐
│ ① 正则规则（结构化凭证，~90% 拦截率）                       │
│   ├ 手机号/身份证/邮箱/银行卡                              │
│   ├ AWS Key (AKIA[0-9A-Z]{16})                           │
│   ├ GitHub Token (ghp_[A-Za-z0-9]{36})                   │
│   ├ OpenAI/Anthropic Key (sk-[A-Za-z0-9]{20,})          │
│   ├ 私钥块 (-----BEGIN ... PRIVATE KEY-----)            │
│   └ Generic API key (api_key=xxx, secret=xxx, token=xxx)│
├──────────────────────────────────────────────────────────┤
│ ② 熵值检测（未知模式的高熵字符串）                          │
│   └ Shannon 熵 ≥ 4.5 + 长度 ≥ 32 字符                    │
│     → 拦截 "看起来像密钥但正则没匹配到" 的情况              │
├──────────────────────────────────────────────────────────┤
│ ③ Luhn 二次验证（防误判）                                  │
│   └ 13-19 位连续数字先过 Luhn 算法                        │
│     → 只把真银行卡号替换，假号码（如 12345678）保留         │
├──────────────────────────────────────────────────────────┤
│ ④ 敏感词库（业务相关）                                     │
│   └ filters/wordlist.txt 加载 → 字面匹配                  │
│     → 拦截项目特有敏感词：客户代号、合同号、内部项目名      │
└──────────────────────────────────────────────────────────┘
   ↓
可选 ⑤ LLM 上下文判断（默认关闭）
   └ "我的密码是 abc123" 这类隐式泄露识别
```

### Why 4 layers (not 1, not 10)?

- **1 layer is too weak**: Regex alone misses creative obfuscation; entropy alone has too many false positives
- **10 layers is over-engineered**: Each additional layer costs maintainability; 4 layers catch >99% of real-world leaks
- **Layered cost**: L1 (regex) is fastest/cheapest → L4 (wordlist) is slowest; ordered by ROI

## Process

### Step 1: 文件白/黑名单分流（前置过滤）

```python
SCAN_EXTS  = {'.md', '.ts', '.tsx', '.js', '.jsx', '.py', '.go',
              '.java', '.kt', '.swift', '.rs', '.txt', '.yaml', '.yml', '.json'}
SKIP_NAMES = {'.env', '.env.*', '.pem', '.key', '.p12', '.pfx',
              'id_rsa', 'id_rsa.pub', 'credentials.json', 'service-account.json'}
SKIP_DIRS  = {'.git', 'node_modules', 'dist', 'build', '__pycache__', '.venv', 'venv'}
```

**规则**:
- 扩展名 ∉ `SCAN_EXTS` → 跳过
- 文件名匹配 `SKIP_NAMES` glob → 跳过（这些文件本身就不该被读）
- 路径含 `SKIP_DIRS` → 跳过

**🚪 决策**: 跳过 ≠ 信任；这些文件应该在更高层（操作系统权限/CI 环境变量管理）被保护，本 skill 不管。

### Step 2: 四道防线执行

调用 `SensitiveFilter.filter_text()`：

```python
from filters.sensitive_filter import SensitiveFilter

f = SensitiveFilter(config_path="filters/wordlist.txt")
sanitized_text, hits = f.filter_text(file_content, context=str(file_path))
```

返回值：
- `sanitized_text` — 脱敏后内容（可安全写入知识库）
- `hits` — 命中列表 `[{type, category, severity, position, hash, ...}]`

### Step 3: 严重度分级处理

| 严重度 | 命中类型示例 | 处理方式 |
|--------|------------|---------|
| **`high`** | 私钥块、AWS Key、GitHub Token、OpenAI Key | **整个文件跳过** + 写 `blocked_files[]` + 暂停等待用户确认 |
| **`medium`** | 手机号、身份证、邮箱、银行卡(Luhn 通过)、内网 IP、Generic API key (含上下文) | 替换为 `<REDACTED:类型>` 占位符 |
| **`low`** | 业务敏感词（来自 `wordlist.txt`） | 替换为 `*` × len(word) |

### Step 4: 审计日志写入

```python
audit_dir = Path(project_root) / "docs/project-note/.audit"
audit_dir.mkdir(parents=True, exist_ok=True)
audit_file = audit_dir / f"filter-{timestamp}.json"

audit_data = {
    "phase": "0.5",
    "skill": "sensitive-info-filter",
    "version": "1.1",
    "scanned_files": 145,
    "redacted_files": 12,
    "blocked_files": 1,
    "skipped_files": 8,
    "hits": [
        {
            "file": "src/api/config.ts",
            "type": "regex",
            "category": "OpenAI Key",
            "severity": "high",
            "position": [128, 180],
            "preview": "sk-abc...901",
            "hash": "a3f5e7c1"  # 8 位原文 hash（只用于追踪，不用于还原）
        }
    ]
}
```

**合规要点**: `preview` 字段是脱敏预览（如 `****901`），**绝不**存原文或全 hash。

### Step 5: 高危 Gate 暂停确认

如果 `blocked_files` 非空，**必须暂停**询问用户：

```
⛔ 敏感信息过滤高危命中

文件：src/config/secrets.ts
命中：1 处 OpenAI Key (severity=high)
预览：sk-abc...901

选项：
  A. 跳过该文件（不写入 docs/project-note/）— 推荐
  B. 人工审核后保留（确认是占位符示例）
  C. 中止整个知识库构建

(请明确选择 A/B/C，笨蛋！)
```

**用户必须明确选择**才能继续。

## 输出物

| 产出 | 路径 | 用途 |
|------|------|------|
| 净化后文件 | 原位覆盖（或输出到指定目录） | 下游 Phase 1 扫描用此版本 |
| 审计日志 | `docs/project-note/.audit/filter-YYYYMMDD-HHMMSS.json` | 合规追溯、安全审计 |
| 净化统计 | 控制台 + 可选 JSON | "本次扫描 145 文件，净化 12，拦截 1，跳过 8" |

## 关键设计决策（trade-off）

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 过滤时机 | ✅ 写入前（源头控制） | 避免泄露到 L1/L2/L3 任何下游 |
| 替换策略 | ✅ 替换为占位符（保留结构） | LLM 仍能理解"这里有手机号字段" |
| 误杀处理 | ✅ 分级（高危=拦截询问，中危=替换，低危=标记） | 严格 vs 宽松的折衷 |
| AI 审核 | ✅ 可选（`--ai-audit` 启用） | 速度快慢权衡，避免默认依赖 LLM |
| 审计日志 | ✅ 必须留（但只存 hash） | 合规需要 + 不二次泄露 |
| 词库更新 | ✅ 手动 + 配置文件 | 避免 AI 自由发挥，加新词需人工 review |
| 跳过文件 | ✅ 记录但不删除 | 保留人工审核机会，而非静默忽略 |

## 性能特征

| 文件大小 | 处理时间 | 备注 |
|---------|---------|------|
| < 10 KB | < 100ms | 单文件扫描 |
| 10-100 KB | 100-500ms | 含多文件批量 |
| 100 KB - 1 MB | 500ms-2s | 启用熵值检测时 |
| > 1 MB | > 2s | 建议分块或仅扫描关键文件 |

**优化建议**:
- 大项目先用 `find` 过滤扩展名 → 只对命中文件调用本 skill
- 熵值检测对超长字符串（> 1MB）可能慢，可设长度上限

## 配置说明

### regex_rules.yaml — 正则规则配置

支持热更新，格式：
```yaml
rules:
  - name: AWS Key
    pattern: 'AKIA[0-9A-Z]{16}'
    severity: high
  - name: GitHub Token
    pattern: 'ghp_[A-Za-z0-9]{36}'
    severity: high
  # ...
```

预置 20+ 条规则（详见 `filters/regex_rules.yaml`）。

### wordlist.txt — 业务敏感词库

每行一个，支持 `#` 开头注释：
```text
# 客户名
客户A
客户B-项目

# 内部代号
Project-X
Phoenix-V2
```

预置默认词库（详见 `filters/wordlist.txt`），按需扩展。

## 与上游/下游 Skill 的集成

```
上游 Skill
  ├─ knowledge-base-builder (Phase 0.5 必调)
  ├─ feat-design-planning (Phase 0 写入需求文档前)
  └─ fix-bug-workflow (Step 9.0 脱敏预扫描 + Step 9.2 晋升前复扫，写 lesson.md / docs/project-note 前)
        ↓ 调用
  sensitive-info-filter (本 skill)
        ↓ 输出
  净化后文件 + 审计日志
        ↓ 进入
  下游 LLM 上下文（已安全）
```

## Red Flags

- ❌ 在生产环境用本 skill 过滤用户输入 → 性能不够，应使用专业 DLP
- ❌ 期望 100% 准确率 → 永远会有漏网之鱼，配合代码 review + secret scanning (如 `gitleaks`)
- ❌ 跳过审计日志 → 合规审计无法追溯
- ❌ 修改审计日志存原文 → 审计本身成为泄露源
- ❌ 在 LLM 上下文中看到 `<REDACTED:手机号>` 后猜测原文 → 永远不要这样做

## 快速使用

### 命令行使用

```bash
# 单文件脱敏
python -m filters.sensitive_filter path/to/file.md

# 批量扫描目录
python -m filters.sensitive_filter --scan path/to/project --output path/to/clean

# 仅检测不修改（dry-run）
python -m filters.sensitive_filter --scan path/to/project --dry-run

# 启用 AI 上下文判断
python -m filters.sensitive_filter path/to/file.md --ai-audit

# 查看帮助
python -m filters.sensitive_filter --help
```

### Python API 使用

```python
from filters.sensitive_filter import SensitiveFilter

f = SensitiveFilter(config_path="filters/wordlist.txt")

# 单文件
with open("src/config.ts", "r") as fp:
    content = fp.read()
sanitized, hits = f.filter_text(content, context="src/config.ts")

if any(h['severity'] == 'high' for h in hits):
    print("⛔ 高危命中，跳过")
else:
    with open("src/config.ts", "w") as fp:
        fp.write(sanitized)
    print(f"✅ 脱敏完成，命中 {len(hits)} 处")
```

### 集成到 pre-commit hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: sensitive-filter
        name: Sensitive Info Filter
        entry: python -m filters.sensitive_filter
        language: system
        files: '\.(md|ts|tsx|js|py|go|java)$'
        pass_filenames: true
```

## 测试

```bash
cd skills/sensitive-info-filter
python -m pytest tests/ -v

# 15+ 测试用例，覆盖：
# - 10+ 凭证类型（API Key/Token/私钥/PII/银行卡）
# - 熵值边界条件
# - Luhn 算法正反向
# - 文件黑白名单分流
# - 高危 Gate 暂停流程
```

## Related Skills

| Skill | 关系 |
|-------|------|
| `knowledge-base-builder` | **直接上游**：Phase 0.5 必调本 skill |
| `feat-design-planning` | **上游**：Phase 0 写入 `docs/vX/设计文档.md` 前可调用 |
| `deep-planning-rigorous-validation` | **上游**：阶段 9 写报告前可调用 |
| `fix-bug-workflow` | **上游**：Step 9.0 写 lesson 前、Step 9.2 晋升 `docs/project-note/` 前必调 |
| `gitleaks` (外部工具) | **互补**：本 skill 拦截代码泄露，gitleaks 拦截 git 历史泄露 |
