"""
backend/contracts/evaluation_contract.py

Evaluation Contract v1 - Pydantic V2 契约模型

对齐契约规范 §4-5，实现：
- Profile、AggregateStatus、EvidenceType、EvidenceStatus、TrustLevel 枚举
- Artifact、EvidenceRef、EvaluationRequest、Finding、Provenance、CanonicalResult 模型
- 自定义 validators 确保数据合法性

对应数据流节点：D（Contract Validator）
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ═══════════════════════════════════════════════════════════════
# 1. 枚举定义
# ═══════════════════════════════════════════════════════════════


class Profile(str, Enum):
    """Profile 枚举 - 由证据门控的评测能力层级"""

    CODE_STATIC = "code-static"
    CODE_VERIFIED = "code-verified"
    AGENT_VERIFIED = "agent-verified"


class AggregateStatus(str, Enum):
    """聚合状态 - 评测结果的整体状态"""

    VALID = "valid"
    PROVISIONAL = "provisional"
    WITHHELD = "withheld"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    INCONCLUSIVE = "inconclusive"


class EvidenceType(str, Enum):
    """Evidence 类型 - 支持评测声明的证据对象类型"""

    CODE = "code"
    DIFF = "diff"
    TASK_SPEC = "task_spec"
    PROJECT_RULES = "project_rules"
    TEST_RESULT = "test_result"
    STATIC_ANALYSIS = "static_analysis"
    PERFORMANCE_SAMPLE = "performance_sample"
    AGENT_TRACE = "agent_trace"
    TOOL_EVENT = "tool_event"
    TOKEN_USAGE = "token_usage"
    FINAL_ANSWER = "final_answer"
    SANDBOX_EXECUTION = "sandbox_execution"
    # v0.7.x：code-verified Profile 的 Oracle Evidence
    CONTRACT_ORACLE = "contract_oracle"
    SCENARIO_ORACLE = "scenario_oracle"


class EvidenceStatus(str, Enum):
    """Evidence 状态"""

    PRESENT = "present"
    ABSENT = "absent"
    INVALID = "invalid"
    REDACTED = "redacted"
    EXPIRED = "expired"
    UNTRUSTED = "untrusted"


class TrustLevel(str, Enum):
    """Evidence 信任级别"""

    UNTRUSTED_INPUT = "untrusted_input"
    HEURISTIC = "heuristic"
    VERIFIED_EXECUTION = "verified_execution"
    SIGNED_TRACE = "signed_trace"


class Severity(str, Enum):
    """Finding 严重度"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ConfidenceKind(str, Enum):
    """置信度类型"""

    HEURISTIC = "heuristic"
    CALIBRATED = "calibrated"
    EMPIRICAL = "empirical"


class DisplayStatus(str, Enum):
    """Finding 显示状态"""

    VISIBLE = "visible"
    HIDDEN = "hidden"
    DIMMED = "dimmed"


# ═══════════════════════════════════════════════════════════════
# 2. 基础模型
# ═══════════════════════════════════════════════════════════════


class Artifact(BaseModel):
    """Artifact - 被评测的代码、Diff、文件集合或项目快照"""

    type: Literal["code", "diff"] = Field(
        ...,
        description="Artifact 类型：code 或 diff",
    )
    path: str = Field(
        ...,
        description="文件路径",
        min_length=1,
    )
    content: str = Field(
        ...,
        description="文件内容",
    )
    encoding: str = Field(
        default="utf-8",
        description="内容编码，默认为 UTF-8",
    )

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        """规范化路径：去除首尾空白"""
        return v.strip()

    @field_validator("content")
    @classmethod
    def normalize_content(cls, v: str) -> str:
        """规范化内容：换行符统一为 LF"""
        return v.replace("\r\n", "\n").replace("\r", "\n")


class Confidence(BaseModel):
    """置信度 - Finding 的可信程度"""

    value: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="置信度值，0.0-1.0",
    )
    kind: ConfidenceKind = Field(
        ...,
        description="置信度类型：heuristic、calibrated、empirical",
    )
    calibration_version: Optional[str] = Field(
        default=None,
        description="校准版本（仅 calibrated 类型适用）",
    )


class FindingSource(BaseModel):
    """Finding 来源 - 指向具体证据和位置"""

    evidence_id: str = Field(
        ...,
        description="关联的 Evidence ID",
    )
    file: str = Field(
        ...,
        description="文件路径",
    )
    line_start: Optional[int] = Field(
        default=None,
        ge=1,
        description="起始行号（正整数）",
    )
    line_end: Optional[int] = Field(
        default=None,
        ge=1,
        description="结束行号（正整数）",
    )
    snippet_hash: Optional[str] = Field(
        default=None,
        description="代码片段哈希",
    )


class Finding(BaseModel):
    """
    Finding - 面向用户的单条风险或建议

    必须可追溯到证据，包含 brooks 4 段式输出：
    - symptom: 在哪里、症状是什么
    - consequence: 不修会怎样
    - remedy: 具体怎么修
    """

    finding_id: str = Field(
        ...,
        description="Finding 唯一标识",
    )
    dimension: str = Field(
        ...,
        description="所属维度（如 maintainability）",
    )
    rule_id: str = Field(
        ...,
        description="规则 ID（如 R03）",
    )
    severity: Severity = Field(
        ...,
        description="严重度：info、warning、error、critical",
    )
    confidence: Confidence = Field(
        ...,
        description="置信度",
    )
    title: str = Field(
        ...,
        description="简短标题",
    )
    symptom: str = Field(
        ...,
        description="症状：在哪里、症状是什么",
    )
    source: FindingSource = Field(
        ...,
        description="来源：指向证据和位置",
    )
    consequence: str = Field(
        ...,
        description="后果：不修会怎样",
    )
    remedy: str = Field(
        ...,
        description="修复建议：具体怎么修",
    )
    status: str = Field(
        default="open",
        description="Finding 状态：open、resolved、wontfix 等",
    )
    display_status: Optional[DisplayStatus] = Field(
        default=None,
        description="显示状态：visible、hidden、dimmed",
    )


# ═══════════════════════════════════════════════════════════════
# 3. Evidence 模型
# ═══════════════════════════════════════════════════════════════


class Producer(BaseModel):
    """Evidence 生产者信息"""

    kind: str = Field(
        ...,
        description="生产者类型：cli、skill、web 等",
    )
    name: str = Field(
        ...,
        description="生产者名称",
    )
    version: str = Field(
        ...,
        description="生产者版本",
    )


class Retention(BaseModel):
    """Evidence 保留策略"""

    policy: str = Field(
        ...,
        description="保留策略：ephemeral、persistent 等",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="过期时间",
    )


class EvidenceRef(BaseModel):
    """
    EvidenceRef - 支持某个评测声明的证据对象

    对齐契约规范 §4.3
    """

    evidence_id: str = Field(
        ...,
        description="Evidence 唯一标识",
    )
    type: EvidenceType = Field(
        ...,
        description="Evidence 类型",
    )
    status: EvidenceStatus = Field(
        ...,
        description="Evidence 状态",
    )
    hash: Optional[str] = Field(
        default=None,
        description="Evidence 内容哈希（sha256:...）",
    )
    source: str = Field(
        ...,
        description="Evidence 来源标识",
    )
    captured_at: datetime = Field(
        ...,
        description="捕获时间",
    )
    artifact_digest: str = Field(
        ...,
        description="关联的 Artifact digest",
    )
    trust_level: TrustLevel = Field(
        default=TrustLevel.UNTRUSTED_INPUT,
        description="信任级别",
    )
    size_bytes: Optional[int] = Field(
        default=None,
        ge=0,
        description="Evidence 大小（字节）",
    )
    environment_id: Optional[str] = Field(
        default=None,
        description="环境标识",
    )
    producer: Optional[Producer] = Field(
        default=None,
        description="生产者信息",
    )
    retention: Optional[Retention] = Field(
        default=None,
        description="保留策略",
    )


# ═══════════════════════════════════════════════════════════════
# 4. 评测请求模型
# ═══════════════════════════════════════════════════════════════


class ConfidenceThreshold(BaseModel):
    """置信度阈值配置"""

    value: int = Field(
        default=0,
        ge=0,
        le=100,
        description="阈值（0-100），默认 0 表示关闭过滤",
    )
    action: Literal["filter", "dim"] = Field(
        default="filter",
        description="过滤动作：filter=隐藏，dim=淡化显示",
    )


class TaskSpec(BaseModel):
    """任务规格"""

    content: str = Field(
        ...,
        description="任务描述内容",
    )
    source: str = Field(
        ...,
        description="任务来源：user_input、project_file 等",
    )


class ProjectRule(BaseModel):
    """项目规则"""

    content: str = Field(
        ...,
        description="规则内容",
    )
    source: str = Field(
        ...,
        description="规则来源：project_file、user_input 等",
    )


class Policy(BaseModel):
    """评测策略配置"""

    allow_remote_llm: bool = Field(
        default=False,
        description="是否允许远程 LLM 调用",
    )
    allow_code_egress: bool = Field(
        default=False,
        description="是否允许代码外传",
    )
    retention: str = Field(
        default="ephemeral",
        description="保留策略",
    )
    secret_scan: str = Field(
        default="strict",
        description="Secret 扫描策略",
    )
    confidence_threshold: ConfidenceThreshold = Field(
        default_factory=ConfidenceThreshold,
        description="置信度阈值配置",
    )


class Budget(BaseModel):
    """评测预算限制"""

    max_input_bytes: int = Field(
        default=2097152,  # 2 MiB
        ge=1,
        description="最大输入字节数",
    )
    max_files: int = Field(
        default=200,
        ge=1,
        description="最大文件数",
    )
    max_duration_ms: int = Field(
        default=30000,
        ge=1000,
        description="最大执行时间（毫秒）",
    )


class EvaluationRequest(BaseModel):
    """
    EvaluationRequest - 评测请求

    对齐契约规范 §5.1
    """

    schema_version: str = Field(
        default="evaluation-contract-v1",
        description="契约版本",
    )
    requested_profile: Profile = Field(
        ...,
        description="请求的 Profile",
    )
    fallback_profile: Optional[Profile] = Field(
        default=None,
        description="回退 Profile（显式提供时才允许）",
    )
    artifacts: List[Artifact] = Field(
        ...,
        description="Artifact 列表",
    )
    task_spec: Optional[TaskSpec] = Field(
        default=None,
        description="任务规格",
    )
    project_rules: Optional[List[ProjectRule]] = Field(
        default=None,
        description="项目规则列表",
    )
    evidence_refs: List[EvidenceRef] = Field(
        default_factory=list,
        description="预提供的 Evidence 引用",
    )
    policy: Optional[Policy] = Field(
        default=None,
        description="评测策略",
    )
    budget: Optional[Budget] = Field(
        default=None,
        description="评测预算",
    )
    baseline_evaluation_id: Optional[str] = Field(
        default=None,
        description="基线评测 ID（用于改进对比）",
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="幂等键",
    )

    @field_validator("artifacts")
    @classmethod
    def validate_artifacts(cls, v: List[Artifact]) -> List[Artifact]:
        """验证 artifacts 至少包含一项"""
        if not v:
            raise ValueError("artifacts must contain at least one item")
        return v


# ═══════════════════════════════════════════════════════════════
# 5. Provenance 模型
# ═══════════════════════════════════════════════════════════════


class ExecutionEnvironment(BaseModel):
    """执行环境信息"""

    id: str = Field(
        ...,
        description="环境标识（如 local:project-root）",
    )
    os: str = Field(
        ...,
        description="操作系统",
    )
    runtime: str = Field(
        ...,
        description="运行时（如 node、python）",
    )
    runtime_version: str = Field(
        ...,
        description="运行时版本",
    )


class Provenance(BaseModel):
    """
    Provenance - 记录评测如何产生、使用了哪些版本和环境

    对齐契约规范 §7
    """

    evaluation_id: str = Field(
        ...,
        description="评测 ID",
    )
    schema_version: str = Field(
        ...,
        description="契约版本",
    )
    profile_id: str = Field(
        ...,
        description="Profile ID",
    )
    profile_version: str = Field(
        ...,
        description="Profile 版本",
    )
    claim_scope: str = Field(
        ...,
        description="声明范围（如 code_snapshot）",
    )
    engine_version: str = Field(
        ...,
        description="引擎版本",
    )
    engine_commit: Optional[str] = Field(
        default=None,
        description="引擎 commit hash",
    )
    rubric_version: Optional[str] = Field(
        default=None,
        description="评分标准版本",
    )
    rubric_hash: Optional[str] = Field(
        default=None,
        description="评分标准哈希",
    )
    weights_version: Optional[str] = Field(
        default=None,
        description="权重版本",
    )
    prompt_version: Optional[str] = Field(
        default=None,
        description="Prompt 版本",
    )
    ruleset_version: str = Field(
        ...,
        description="规则集版本",
    )
    calibration_version: Optional[str] = Field(
        default=None,
        description="校准版本",
    )
    provider: Optional[str] = Field(
        default=None,
        description="LLM 提供商",
    )
    requested_model: Optional[str] = Field(
        default=None,
        description="请求的模型",
    )
    resolved_model: Optional[str] = Field(
        default=None,
        description="实际使用的模型",
    )
    temperature: Optional[float] = Field(
        default=None,
        description="温度参数",
    )
    max_tokens: Optional[int] = Field(
        default=None,
        description="最大 token 数",
    )
    seed: Optional[int] = Field(
        default=None,
        description="随机种子",
    )
    seed_supported: bool = Field(
        default=False,
        description="是否支持种子",
    )
    repetitions: int = Field(
        default=1,
        ge=1,
        description="重复次数",
    )
    raw_response_hashes: List[str] = Field(
        default_factory=list,
        description="原始响应哈希列表",
    )
    request_ids: List[str] = Field(
        default_factory=list,
        description="请求 ID 列表",
    )
    token_usage: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Token 使用统计",
    )
    cost: Optional[float] = Field(
        default=None,
        description="成本",
    )
    artifact_digest: str = Field(
        ...,
        description="Artifact digest",
    )
    repository_commit: Optional[str] = Field(
        default=None,
        description="仓库 commit hash",
    )
    execution_environment: ExecutionEnvironment = Field(
        ...,
        description="执行环境信息",
    )


# ═══════════════════════════════════════════════════════════════
# 6. Canonical Result 模型
# ═══════════════════════════════════════════════════════════════


class ImprovementComparison(BaseModel):
    """
    ImprovementComparison - 改进对比结果

    对齐契约规范 §6.4
    """

    baseline_evaluation_id: str = Field(
        ...,
        description="基线评测 ID",
    )
    baseline_aggregate_status: AggregateStatus = Field(
        ...,
        description="基线聚合状态",
    )
    current_evaluation_id: str = Field(
        ...,
        description="当前评测 ID",
    )
    current_aggregate_status: AggregateStatus = Field(
        ...,
        description="当前聚合状态",
    )
    artifact_changed: bool = Field(
        ...,
        description="Artifact 是否变化",
    )
    delta_findings: Dict[str, List[str]] = Field(
        ...,
        description="Finding 变化：resolved、new、unchanged、regressed",
    )
    summary: Dict[str, int] = Field(
        ...,
        description="汇总统计",
    )


class CanonicalResult(BaseModel):
    """
    CanonicalResult - 规范评测结果

    唯一规范输出，CLI/Web/Skill 都是其派生视图
    """

    evaluation_id: str = Field(
        ...,
        description="评测 ID",
    )
    requested_profile: Profile = Field(
        ...,
        description="请求的 Profile",
    )
    effective_profile: Profile = Field(
        ...,
        description="实际执行的 Profile",
    )
    claim_scope: str = Field(
        ...,
        description="声明范围",
    )
    aggregate_status: AggregateStatus = Field(
        ...,
        description="聚合状态",
    )
    findings: List[Finding] = Field(
        default_factory=list,
        description="Finding 列表",
    )
    evidence_manifest: List[EvidenceRef] = Field(
        default_factory=list,
        description="Evidence Manifest",
    )
    provenance: Provenance = Field(
        ...,
        description="Provenance 信息",
    )
    limitations: List[str] = Field(
        default_factory=list,
        description="限制说明",
    )
    improvement_comparison: Optional[ImprovementComparison] = Field(
        default=None,
        description="改进对比结果（可选）",
    )
    # v0.5.1: L4 Progress Rate（Agent 进度率，可选）
    l4: Optional[Dict[str, Any]] = Field(
        default=None,
        description="L4 Progress Rate（score/completed_steps/total_steps/steps）",
    )
    # v0.5.1: L5 多维细粒度评分（可选）
    l5: Optional[Dict[str, Any]] = Field(
        default=None,
        description="L5 多维评分（overall_score/dimensions）",
    )
    # v0.5.1: Hermes Agent LLM 语义增强分析（可选）
    llm_analysis: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Hermes Agent LLM 语义增强分析结果（FindingAnalysis 列表）",
    )


# ═══════════════════════════════════════════════════════════════
# 7. 错误响应模型
# ═══════════════════════════════════════════════════════════════


class ErrorDetail(BaseModel):
    """错误详情"""

    code: str = Field(
        ...,
        description="错误码",
    )
    message: str = Field(
        ...,
        description="错误消息",
    )
    details: Optional[str] = Field(
        default=None,
        description="详细错误信息",
    )


class ErrorResponse(BaseModel):
    """错误响应"""

    error: ErrorDetail = Field(
        ...,
        description="错误详情",
    )
