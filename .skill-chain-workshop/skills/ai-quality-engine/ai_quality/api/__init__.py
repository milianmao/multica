"""
backend/api/__init__.py

Web API 模块 - FastAPI 应用入口

对应数据流节点：
- C（Web Adapter）：FastAPI 异步接口
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_quality.api.evaluations import router as evaluations_router
from ai_quality.api.jobs import router as jobs_router

# 创建 FastAPI 应用
app = FastAPI(
    title="AI Coding 质量评测服务",
    description="基于 Evidence-gated Evaluation 的代码质量评测平台",
    version="0.2.2",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应配置具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(evaluations_router)
app.include_router(jobs_router)


@app.get("/", tags=["root"])
async def root():
    """根路径 - 服务信息"""
    return {
        "service": "AI Coding 质量评测服务",
        "version": "0.2.2",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/health", tags=["health"])
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "version": "0.2.2",
    }


# 保留 api_router 以兼容旧代码
from fastapi import APIRouter
api_router = APIRouter()
api_router.include_router(evaluations_router)
api_router.include_router(jobs_router)

__all__ = ["app", "api_router"]
