"""
backend/api/jobs.py

Job 查询 API 端点 - 提供 Job 状态查询功能

对应数据流节点：
- C（Web Adapter）：FastAPI 异步接口

端点：
- GET /api/v1/jobs/{job_id}: 查询 Job 状态

约束：
- GET 只允许租户访问自身 Job
- 支持 queued/running/completed/failed/canceled/expired 状态
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Header, status
from pydantic import BaseModel

from ai_quality.api.evaluations import orchestrator
from ai_quality.services.job_orchestrator import JobStatus

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ═══════════════════════════════════════════════════════════════
# 响应模型
# ═══════════════════════════════════════════════════════════════


class JobStatusResponse(BaseModel):
    """Job 状态响应"""
    job_id: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# API 端点
# ═══════════════════════════════════════════════════════════════


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="查询 Job 状态",
    description="查询 Job 的当前状态",
)
async def get_job(
    job_id: str,
    x_tenant_id: str = Header(default="default_tenant", description="租户 ID"),
):
    """
    查询 Job 状态

    - 只允许租户访问自身 Job
    - 返回 Job 状态信息
    """
    job = orchestrator.get_job(x_tenant_id, job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="job not found",
        )

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        error=job.error,
    )


@router.get(
    "/",
    summary="列出租户的所有 Job",
    description="列出当前租户的所有评测 Job",
)
async def list_jobs(
    x_tenant_id: str = Header(default="default_tenant", description="租户 ID"),
):
    """
    列出租户的所有 Job

    - 只返回当前租户的 Job
    """
    jobs = orchestrator.list_jobs(x_tenant_id)

    return {
        "jobs": [
            {
                "job_id": job.job_id,
                "status": job.status,
                "created_at": job.created_at.isoformat(),
            }
            for job in jobs
        ],
        "total": len(jobs),
    }
