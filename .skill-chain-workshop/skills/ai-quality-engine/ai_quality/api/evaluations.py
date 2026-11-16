"""
backend/api/evaluations.py

评测 API 端点 - 处理评测任务的创建、查询和取消

对应数据流节点：
- C（Web Adapter）：FastAPI 异步接口

端点：
- POST /api/v1/evaluations: 创建评测任务（202 + evaluationId）
- GET /api/v1/evaluations/{evaluationId}: 查询评测结果
- POST /api/v1/evaluations/{evaluationId}/cancel: 取消评测任务

约束（REQ-ADAPTER-03）：
- POST 返回 202 和 evaluationId
- 相同 Idempotency-Key + 相同请求只创建一个 Job
- GET 只允许租户访问自身 Job
- 支持 queued/running/completed/failed/canceled/expired 状态
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Header, status
from pydantic import BaseModel, Field

from ai_quality.services.job_orchestrator import JobOrchestrator, JobStatus

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

# 全局 Job Orchestrator（单例）
# 注意：生产环境应使用依赖注入
orchestrator = JobOrchestrator()


# ═══════════════════════════════════════════════════════════════
# 请求/响应模型
# ═══════════════════════════════════════════════════════════════


class ArtifactRequest(BaseModel):
    """Artifact 请求模型"""
    type: str = Field(..., description="Artifact 类型：code 或 diff")
    path: str = Field(..., description="文件路径")
    content: str = Field(..., description="文件内容")
    encoding: str = Field(default="utf-8", description="内容编码")


class TaskSpecRequest(BaseModel):
    """任务规格请求模型"""
    content: str = Field(..., description="任务描述内容")
    source: str = Field(..., description="任务来源")


class ProjectRuleRequest(BaseModel):
    """项目规则请求模型"""
    content: str = Field(..., description="规则内容")
    source: str = Field(..., description="规则来源")


class ConfidenceThresholdRequest(BaseModel):
    """置信度阈值配置"""
    value: int = Field(default=0, ge=0, le=100, description="阈值 0-100")
    action: str = Field(default="filter", description="filter 或 dim")


class PolicyRequest(BaseModel):
    """评测策略配置"""
    allow_remote_llm: bool = Field(default=False, description="是否允许远程 LLM")
    allow_code_egress: bool = Field(default=False, description="是否允许代码外传")
    retention: str = Field(default="ephemeral", description="保留策略")
    secret_scan: str = Field(default="strict", description="Secret 扫描策略")
    confidence_threshold: ConfidenceThresholdRequest = Field(
        default_factory=ConfidenceThresholdRequest,
        description="置信度阈值配置",
    )


class BudgetRequest(BaseModel):
    """评测预算限制"""
    max_input_bytes: int = Field(default=2097152, ge=1, description="最大输入字节数")
    max_files: int = Field(default=200, ge=1, description="最大文件数")
    max_duration_ms: int = Field(default=30000, ge=1000, description="最大执行时间（毫秒）")


class EvaluationCreateRequest(BaseModel):
    """创建评测请求"""
    schema_version: str = Field(default="evaluation-contract-v1", description="契约版本")
    requested_profile: str = Field(default="code-static", description="请求的 Profile")
    fallback_profile: Optional[str] = Field(default=None, description="回退 Profile")
    artifacts: List[ArtifactRequest] = Field(..., description="Artifact 列表")
    task_spec: Optional[TaskSpecRequest] = Field(default=None, description="任务规格")
    project_rules: Optional[List[ProjectRuleRequest]] = Field(default=None, description="项目规则列表")
    policy: Optional[PolicyRequest] = Field(default=None, description="评测策略")
    budget: Optional[BudgetRequest] = Field(default=None, description="评测预算")
    baseline_evaluation_id: Optional[str] = Field(default=None, description="基线评测 ID")


class JobResponse(BaseModel):
    """Job 状态响应"""
    status: str = Field(..., description="Job 状态")
    created_at: str = Field(..., description="创建时间")
    started_at: Optional[str] = Field(default=None, description="开始时间")
    finished_at: Optional[str] = Field(default=None, description="完成时间")


class EvaluationCreateResponse(BaseModel):
    """创建评测响应"""
    evaluation_id: str = Field(..., description="评测 ID")
    job: Dict[str, Any] = Field(..., description="Job 状态信息")
    requested_profile: str = Field(..., description="请求的 Profile")
    links: Dict[str, str] = Field(..., description="相关链接")


class EvaluationGetResponse(BaseModel):
    """查询评测响应"""
    evaluation_id: str = Field(..., description="评测 ID")
    job: JobResponse = Field(..., description="Job 状态信息")
    result: Optional[Dict[str, Any]] = Field(default=None, description="评测结果")
    error: Optional[str] = Field(default=None, description="错误信息")


# ═══════════════════════════════════════════════════════════════
# API 端点
# ═══════════════════════════════════════════════════════════════


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EvaluationCreateResponse,
    summary="创建评测任务",
    description="创建异步评测任务，返回 202 和 evaluationId",
)
async def create_evaluation(
    request: EvaluationCreateRequest,
    x_tenant_id: str = Header(default="default_tenant", description="租户 ID"),
    x_idempotency_key: Optional[str] = Header(default=None, description="幂等键"),
):
    """
    创建评测任务（异步）

    - 返回 202 Accepted
    - 相同 Idempotency-Key + 相同租户只创建一个 Job
    - Job 状态初始为 queued
    """
    try:
        # 创建 Job
        job = await orchestrator.create_job(
            tenant_id=x_tenant_id,
            request=request.model_dump(),
            idempotency_key=x_idempotency_key,
        )

        return EvaluationCreateResponse(
            evaluation_id=job.job_id,
            job={
                "status": job.status,
                "created_at": job.created_at.isoformat(),
            },
            requested_profile=request.requested_profile,
            links={
                "self": f"/api/v1/evaluations/{job.job_id}",
                "capabilities": "/api/v1/capabilities",
            },
        )

    except ValueError as e:
        # 契约错误返回 400
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        # 内部错误返回 500
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"internal error: {str(e)}",
        )


@router.get(
    "/{evaluation_id}",
    response_model=EvaluationGetResponse,
    summary="查询评测结果",
    description="查询评测任务状态和结果",
)
async def get_evaluation(
    evaluation_id: str,
    x_tenant_id: str = Header(default="default_tenant", description="租户 ID"),
):
    """
    查询评测结果

    - 只允许租户访问自身 Job
    - queued/running 状态下 result 为 null
    - completed 状态下返回完整 result
    - failed 状态下返回 error
    """
    job = orchestrator.get_job(x_tenant_id, evaluation_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="evaluation not found",
        )

    response = EvaluationGetResponse(
        evaluation_id=job.job_id,
        job=JobResponse(
            status=job.status,
            created_at=job.created_at.isoformat(),
            started_at=job.started_at.isoformat() if job.started_at else None,
            finished_at=job.finished_at.isoformat() if job.finished_at else None,
        ),
        result=None,
        error=None,
    )

    # 根据状态返回结果
    if job.status == JobStatus.COMPLETED and job.result:
        # 返回完整结果
        result_dict = job.result.model_dump()
        response.result = result_dict
    elif job.status == JobStatus.FAILED:
        response.error = job.error

    return response


@router.post(
    "/{evaluation_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    summary="取消评测任务",
    description="取消尚未完成的评测 Job",
)
async def cancel_evaluation(
    evaluation_id: str,
    x_tenant_id: str = Header(default="default_tenant", description="租户 ID"),
):
    """
    取消评测任务

    - 只有 queued 或 running 状态的 Job 可以取消
    - 已完成、已失败、已过期的 Job 返回 400
    """
    success = orchestrator.cancel_job(x_tenant_id, evaluation_id)

    if not success:
        # 检查 Job 是否存在
        job = orchestrator.get_job(x_tenant_id, evaluation_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="evaluation not found",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cannot cancel job in current status",
            )

    return {"status": "cancel_requested"}
