"""
backend/services/job_orchestrator.py

Job 编排服务 - 管理评测任务的完整生命周期

对应数据流节点：
- E（Job Orchestrator）：任务排队、幂等、取消、超时和状态迁移

职责：
1. Job 创建与管理（幂等键支持）
2. Job 执行编排（异步任务调度）
3. 状态机管理（queued → running → completed/failed/canceled/expired）
4. 租户隔离（GET 只允许租户访问自身 Job）

状态机设计：
- queued: 任务已创建，等待执行
- running: 任务正在执行
- completed: 任务成功完成
- failed: 任务执行失败
- canceled: 任务被取消
- expired: 任务超时过期

幂等键实现策略：
- 使用 idempotency_key 作为唯一标识
- 相同 idempotency_key + 相同租户 → 返回已存在的 Job
- 不同请求使用相同 idempotency_key → 返回冲突错误
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ai_quality.contracts.evaluation_contract import CanonicalResult, Profile
from ai_quality.engine.evaluation_core import EvaluationCore


class JobStatus:
    """Job 状态常量"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"


class Job:
    """
    Job 实体 - 表示一个评测任务

    Attributes:
        job_id: 唯一标识
        tenant_id: 租户 ID（租户隔离）
        request: 原始请求数据
        status: 当前状态
        result: 评测结果（完成后）
        created_at: 创建时间
        started_at: 开始执行时间
        finished_at: 完成时间
        error: 错误信息（失败时）
    """

    def __init__(self, job_id: str, tenant_id: str, request: Dict[str, Any]):
        self.job_id = job_id
        self.tenant_id = tenant_id
        self.request = request
        self.status = JobStatus.QUEUED
        self.result: Optional[CanonicalResult] = None
        self.created_at = datetime.now(timezone.utc)
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典表示"""
        return {
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }


class JobOrchestrator:
    """
    Job 编排服务

    负责：
    - Job 创建（幂等）
    - Job 执行调度
    - 状态管理
    - 租户隔离

    设计原则：
    - 内存存储（生产环境应使用 Redis + 持久化）
    - 异步执行（asyncio.create_task）
    - 租户隔离（所有查询必须验证 tenant_id）
    """

    def __init__(self, core: Optional[EvaluationCore] = None):
        """
        初始化 Job Orchestrator

        Args:
            core: EvaluationCore 实例（可注入，便于测试）
        """
        self.jobs: Dict[str, Job] = {}
        self.core = core or EvaluationCore()
        # 幂等键映射：key -> job_id
        self.idempotency_keys: Dict[str, str] = {}
        # 用于生成唯一 Job ID 的计数器
        self._counter = 0
        # 锁，用于线程安全
        self._lock = asyncio.Lock()

    def _generate_job_id(self) -> str:
        """生成唯一 Job ID"""
        self._counter += 1
        return f"job_{self._counter:012d}"

    async def create_job(
        self,
        tenant_id: str,
        request: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> Job:
        """
        创建 Job（幂等）

        幂等键策略：
        1. 如果提供 idempotency_key 且已存在 → 返回已存在的 Job
        2. 如果不提供 idempotency_key → 总是创建新 Job

        Args:
            tenant_id: 租户 ID
            request: 评测请求数据
            idempotency_key: 幂等键（可选）

        Returns:
            Job 实例

        Raises:
            ValueError: 请求数据无效
        """
        async with self._lock:
            # 幂等键检查
            if idempotency_key:
                if idempotency_key in self.idempotency_keys:
                    existing_job_id = self.idempotency_keys[idempotency_key]
                    existing_job = self.jobs.get(existing_job_id)
                    if existing_job and existing_job.tenant_id == tenant_id:
                        return existing_job
                    else:
                        # 不同租户使用相同 idempotency_key
                        raise ValueError("idempotency_key conflict")

            # 创建新 Job
            job_id = self._generate_job_id()
            job = Job(job_id, tenant_id, request)
            self.jobs[job_id] = job

            if idempotency_key:
                self.idempotency_keys[idempotency_key] = job_id

        # 异步执行（在锁外）
        asyncio.create_task(self._execute_job(job_id))

        return job

    async def _execute_job(self, job_id: str):
        """
        执行 Job

        执行流程：
        1. 更新状态为 running
        2. 调用 EvaluationCore.evaluate
        3. 更新结果和状态

        Args:
            job_id: Job ID
        """
        job = self.jobs.get(job_id)
        if not job:
            return

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)

        try:
            # 执行评测
            artifacts = job.request.get("artifacts", [])
            requested_profile_str = job.request.get("requested_profile", "code-static")

            # 验证 Profile
            try:
                requested_profile = Profile(requested_profile_str)
            except ValueError:
                raise ValueError(f"unknown profile: {requested_profile_str}")

            # 调用 Core 执行评测
            result = await self.core.evaluate(
                artifacts=artifacts,
                requested_profile=requested_profile,
            )

            job.result = result
            job.status = JobStatus.COMPLETED

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)

        finally:
            job.finished_at = datetime.now(timezone.utc)

    def get_job(self, tenant_id: str, job_id: str) -> Optional[Job]:
        """
        获取 Job（租户隔离）

        Args:
            tenant_id: 租户 ID
            job_id: Job ID

        Returns:
            Job 实例（如果存在且属于该租户），否则 None
        """
        job = self.jobs.get(job_id)
        if job and job.tenant_id == tenant_id:
            return job
        return None

    def cancel_job(self, tenant_id: str, job_id: str) -> bool:
        """
        取消 Job

        只有 queued 或 running 状态的 Job 可以取消。

        Args:
            tenant_id: 租户 ID
            job_id: Job ID

        Returns:
            是否成功取消
        """
        job = self.get_job(tenant_id, job_id)
        if job and job.status in [JobStatus.QUEUED, JobStatus.RUNNING]:
            job.status = JobStatus.CANCELED
            job.finished_at = datetime.now(timezone.utc)
            return True
        return False

    def list_jobs(self, tenant_id: str) -> list:
        """
        列出租户的所有 Job

        Args:
            tenant_id: 租户 ID

        Returns:
            Job 列表
        """
        return [
            job for job in self.jobs.values()
            if job.tenant_id == tenant_id
        ]
