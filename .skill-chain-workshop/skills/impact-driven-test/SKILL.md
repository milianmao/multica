---
name: impact-driven-test
version: "1.0"
description: >-
  影响面驱动的自动化测试选择系统。根据代码变更范围，自动分析影响面，
  智能选择最相关的测试用例。支持 Git/SVN 双版本控制系统。
  触发场景：PR测试、版本发布测试、每日增量测试、Hotfix测试。
  关键词：影响面分析、测试选择、智能检测、精准测试、CI/CD集成。
evaluated_by:
  - IDT-E-001
  - IDT-M-001
license: MIT
compatibility: Requires Python >= 3.8, pyyaml, Git
metadata:
  author: caihua
  version: "1.0"
---

# 影响面驱动测试选择系统 (Impact-Driven Test Selection)

## 核心定位

本 Skill 为 AI Coding Agent 提供**精准化、影响面驱动的测试选择能力**。通过分析代码变更的实际影响范围，自动匹配并执行最相关的测试用例，避免"全量测试"的资源浪费和"无测试"的风险。

## 使用场景

| 场景 | 触发时机 | 说明 |
|------|---------|------|
| PR 测试 | 提PR时 | 对比 PR 分支与目标分支 |
| 版本测试 | 打 Tag 时 | 对比两个版本之间的差异 |
| 增量测试 | 定时触发 | 检测上次构建后的变更 |
| Hotfix 测试 | 紧急修复时 | 快速确认影响范围 |
| 本地开发 | 本地调试 | 分析工作目录的变更 |

## 使用方式

### 基本用法

```bash
# 自动检测 VCS 类型，获取变更影响并选择测试
python run_impact_test.py --since-version v2.0.0

# 指定 PR 模式测试
python run_impact_test.py --mode pr --pr-number 123

# 指定 Tag 区间测试
python run_impact_test.py --mode tag --base v2.0.0 --head v2.1.0

# 指定 Commit 区间测试
python run_impact_test.py --mode commit-range --base abc123 --head def456

# 单个 Commit 测试
python run_impact_test.py --mode single-commit --head HEAD~1

# 本地工作目录变更检测
python run_impact_test.py --mode local

# 仅分析影响面，不执行测试
python run_impact_test.py --analyze-only
```

### 在 Claude Code 中使用

```
/impact-test --since v2.0.0
/impact-test --mode pr --pr 123
/impact-test --analyze-only
```

## 核心流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        触发层 (Triggers)                          │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐               │
│  │ PR触发  │ │ 版本发布 │ │ 定时触发 │ │ 手动触发 │               │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘               │
└───────┼──────────┼──────────┼──────────┼───────────────────────────┘
        │          │          │          │
        ▼          ▼          ▼          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    VCS 抽象层 (Git/SVN 统一接口)                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                 VersionControlAdapter                      │   │
│  │         GitAdapter          │          SVNAdapter         │   │
│  └─────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      1️⃣ 变更检测层                               │
│  - 代码扫描：新增/修改了哪些函数、类、接口                          │
│  - 依赖解析：依赖链追溯                                            │
│  - 变更画像：文件数、行数、变更类型                                 │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      2️⃣ 影响面分析层                             │
│  - 影响链路：顺着依赖链向上追溯，找到受影响的业务流程                  │
│  - 影响类型：读、写、计算、外部调用、存储                           │
│  - 风险评级：高/中/低 综合评分                                     │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      3️⃣ 测试选择层                               │
│  - 测试库存：每个业务流程对应的测试用例集                           │
│  - 影响匹配：根据影响面匹配最相关的测试用例                         │
│  - 优先级排序：高风险 → 必须执行，中风险 → 建议执行                │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      4️⃣ 执行与报告层                             │
│  - 只跑被选中的测试用例                                            │
│  - 生成变更影响报告：覆盖了哪些影响面，未覆盖哪些                   │
│  - 输出：可执行的测试命令、预计耗时、通过率预测                     │
└─────────────────────────────────────────────────────────────────┘
```

## 自动配置生成

### 从知识库生成 core_workflows.yaml

如果项目有知识库文档，建议通过 `query-knowledge` 检索业务流文档后手动整理，或使用自动配置生成脚本。

**方式 A：用 query-knowledge 检索业务流文档**（推荐，无需 auto_config.py 脚本）：

```bash
# 1. 获取 KB 索引，确认业务流分类存在
python3 skills/query-knowledge/scripts/query_knowledge.py \
    --repo . --backend project-note --json overview

# 2. 检索所有业务流文档（含 source_files 信息）
python3 skills/query-knowledge/scripts/query_knowledge.py \
    --repo . --backend project-note --json \
    search "业务流 数据流 派发 状态机" --top 10 --context feature

# 3. 按分类列出（如果 overview 显示有 03-核心数据业务流 分类）
python3 skills/query-knowledge/scripts/query_knowledge.py \
    --repo . --backend project-note --json \
    category "03-核心数据业务流"

# 4. 对每个业务流文档 read 全文，提取 source_files + steps
python3 skills/query-knowledge/scripts/query_knowledge.py \
    --repo . --backend project-note \
    read "docs/project-note/03-核心数据业务流/<具体业务流>.md"
```

根据检索结果手动整理为 `config/core_workflows.yaml`。

**方式 B：使用 auto_config.py 脚本**（如果项目自带）：

```bash
python scripts/auto_config.py --repo-path /path/to/project
```

脚本会：
1. 扫描知识库中的业务流文档
2. 提取 `source_files`（每个业务流涉及的文件）
3. 生成 `config/core_workflows.yaml`

### 手动配置

也可以手动创建 `config/core_workflows.yaml`：

```yaml
core_workflows:
  order_create:
    id: WF_ORDER_CREATE
    description: 订单创建流程
    criticality: P0
    requires_transaction: true
    source_files:
      - backend/services/order_service.py
      - backend/api/orders.py
      - backend/models/order.py
    steps:
      - validate_params
      - check_inventory
      - create_order
      - publish_event
    external_calls:
      - PaymentGateway

  user_auth:
    id: WF_USER_AUTH
    description: 用户认证流程
    criticality: P0
    source_files:
      - backend/services/auth_service.py
      - backend/api/auth.py
    steps:
      - validate_credentials
      - generate_token
```

## 配置说明

### 核心流程配置

```yaml
# config/core_workflows.yaml
core_workflows:
  <workflow_id>:
    id: <唯一标识>
    description: <流程描述>
    criticality: P0|P1|P2
    requires_transaction: true|false
    source_files:        # 关键：直接的文件路径映射
      - path/to/file1.py
      - path/to/file2.py
    steps:
      - step1
      - step2
    external_calls:
      - ExternalAPI
```

### 测试策略配置

```yaml
# config/test_policies.yaml
test_policies:
  selection:
    thresholds:
      mandatory_priority: 9   # 优先级 >= 9 必须执行
      recommended_priority: 7  # 7 <= 优先级 < 9 建议执行
      optional_priority: 5     # 5 <= 优先级 < 7 可选执行
    
    risk_thresholds:
      high_risk_score: 7.0    # 影响评分 >= 7 为高风险
      medium_risk_score: 4.0  # 4 <= 评分 < 7 为中风险
    
    max_total_tests: 50      # 最大总测试数
  
  execution:
    order:
      - mandatory
      - recommended
      - optional
```

## 变更检测模式

| 模式 | 触发时机 | 对比基准 | 适用场景 |
|------|---------|---------|---------|
| `pr` | 提PR时 | `main...PR_HEAD` | 单一PR测试，精准 |
| `tag` | 打Tag时 | `v2.0.0...v2.1.0` | 版本发布测试 |
| `commit-range` | 手动触发 | `base...head` | 任意区间测试 |
| `single-commit` | push时 | `HEAD~1...HEAD` | 单提交测试 |
| `incremental` | 定时触发 | `last_build...HEAD` | 每日增量测试 |
| `local` | 本地开发 | 工作目录变更 | 开发自测 |

## 输出示例

```bash
$ python run_impact_test.py --since-version v2.0.0

# 🆕 变更检测 (v2.0.0 → v2.1.0)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📁 12 个文件被修改
# ➕ 450 行新增 | ➖ 120 行删除
# 🆕 新增函数: calculateRiskScore, validateOrderRefund
# ✏️  修改函数: OrderService.createOrder, InventoryService.deductStock

# 🎯 影响面分析
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚠️  4 个核心流程受影响
# 🔴 高风险流程: WF_ORDER_CREATE (影响评分: 9.2)
# 🟡 中风险流程: WF_ORDER_REFUND (影响评分: 6.5)
# 🟡 中风险流程: WF_PRODUCT_QUERY (影响评分: 5.1)
# 🟢 低风险流程: WF_USER_AUTH (影响评分: 2.0)

# 🧪 测试选择
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📊 总计选择 15 个测试用例
#    🔴 必须执行: 6 个 (预计 45 秒)
#    🟡 建议执行: 7 个 (预计 2 分钟)
#    🟢 可选执行: 2 个 (预计 1 分钟)

# ▶️ 开始执行...
#    [✓] TC_ORDER_001 正常下单流程 (3.2s) - PASSED
#    [✓] TC_ORDER_002 库存不足时下单 (2.8s) - PASSED
#    ...

# 📊 测试报告
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 通过率: 13/15 (86.7%)
# 失败: 2 个
#    ❌ TC_ORDER_003: 并发测试发现潜在超卖问题
#    ❌ TC_REFUND_002: 退款后库存未正确释放
```

## 项目适配

### 适配步骤

1. **创建配置目录**
   ```bash
   mkdir -p config/
   ```

2. **创建 core_workflows.yaml**
   
   方式A - 从知识库自动生成：
   ```bash
   python scripts/auto_config.py --repo-path .
   ```
   
   方式B - 手动创建（参考上方示例）

3. **可选：创建 test_policies.yaml**
   ```bash
   cp default_policies.yaml config/test_policies.yaml
   ```

### 项目结构要求

```
project/
├── .claude/
│   └── skills/
│       └── impact-driven-test/
│           ├── SKILL.md
│           ├── run_impact_test.py      # 主入口
│           ├── config/
│           │   ├── core_workflows.yaml  # 核心流程配置
│           │   └── test_policies.yaml    # 测试策略（可选）
│           └── src/                      # 源码（通常通过 submodule 引用）
```

## 依赖要求

```
Python >= 3.8
pyyaml >= 6.0
Git 或 SVN
```

## 扩展指南

### 添加新的 VCS 类型

在 `src/vcs/` 目录下创建新的适配器：

```python
# src/vcs/hg_adapter.py
from .base import VCSAdapter

class HgAdapter(VCSAdapter):
    def detect_changes(self, base_ref: str, head_ref: str) -> List[FileChange]:
        # 实现 Hg 的变更检测逻辑
        pass
```

### 自定义测试执行器

```python
from src.execution.base import TestExecutor

class CustomExecutor(TestExecutor):
    def execute(self, test: TestCase) -> TestResult:
        # 自定义执行逻辑
        pass
    
    def can_execute(self, test: TestCase) -> bool:
        # 检查是否可以执行
        pass
```

## 相关 Skill

- `code-review`: 代码审查流程
- `openspec-apply-change`: OpenSpec 变更实现
- `openspec-verify-change`: OpenSpec 变更验证
