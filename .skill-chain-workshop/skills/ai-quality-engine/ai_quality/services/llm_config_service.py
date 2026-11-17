"""
backend/services/llm_config_service.py

LLM 配置管理服务

实现功能：
- 创建 LLM 配置（加密存储 API Key）
- 获取 LLM 配置（不返回明文 API Key）
- 删除 LLM 配置
- 测试 LLM 连接

对应数据流节点：J（LLM Judge）
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class LLMConfigCreate(BaseModel):
    """创建 LLM 配置请求"""

    base_url: str
    api_key: str
    model: str = "gpt-4"
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=100, le=100000)
    timeout: int = Field(default=60, ge=5, le=300)

    @field_validator("base_url")
    def validate_base_url(cls, v: str) -> str:
        """验证 base_url 格式"""
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return v.rstrip("/")


class LLMConfigResponse(BaseModel):
    """LLM 配置响应（不含 api_key）"""

    id: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    is_active: bool
    has_api_key: bool = True
    created_at: datetime
    updated_at: datetime


class LLMConfigService:
    """LLM 配置管理服务（内存存储，实际应使用 PostgreSQL）"""

    def __init__(self, encryption_service):
        """
        初始化 LLM 配置服务

        Args:
            encryption_service: 加密服务实例
        """
        self.encryption = encryption_service
        # 内存存储（演示用，实际应使用数据库）
        self.configs: Dict[str, Dict[str, Any]] = {}

    def create_config(self, tenant_id: str, config: LLMConfigCreate) -> LLMConfigResponse:
        """
        创建 LLM 配置

        Args:
            tenant_id: 租户 ID
            config: LLM 配置

        Returns:
            创建的配置响应（不含 api_key）
        """
        config_id = f"llm_cfg_{uuid.uuid4().hex[:12]}"
        now = datetime.now()

        # 加密 API Key
        encrypted_api_key = self.encryption.encrypt(config.api_key)

        # 存储
        self.configs[config_id] = {
            "id": config_id,
            "tenant_id": tenant_id,
            "base_url": config.base_url,
            "encrypted_api_key": encrypted_api_key,
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "timeout": config.timeout,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }

        return self._to_response(self.configs[config_id])

    def get_config(self, tenant_id: str, config_id: str) -> Optional[LLMConfigResponse]:
        """
        获取 LLM 配置（不含 api_key）

        Args:
            tenant_id: 租户 ID
            config_id: 配置 ID

        Returns:
            配置响应，如果不存在或无权限则返回 None
        """
        config = self.configs.get(config_id)

        if not config or config["tenant_id"] != tenant_id:
            return None

        return self._to_response(config)

    def get_active_config(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """
        获取租户的活跃配置（包含解密的 api_key）

        Args:
            tenant_id: 租户 ID

        Returns:
            包含解密 api_key 的配置字典，如果不存在则返回 None
        """
        for config in self.configs.values():
            if config["tenant_id"] == tenant_id and config["is_active"]:
                # 解密 api_key
                config_copy = config.copy()
                config_copy["api_key"] = self.encryption.decrypt(config["encrypted_api_key"])
                return config_copy

        return None

    def delete_config(self, tenant_id: str, config_id: str) -> bool:
        """
        删除 LLM 配置

        Args:
            tenant_id: 租户 ID
            config_id: 配置 ID

        Returns:
            是否删除成功
        """
        config = self.configs.get(config_id)

        if not config or config["tenant_id"] != tenant_id:
            return False

        del self.configs[config_id]
        return True

    def _to_response(self, config: Dict[str, Any]) -> LLMConfigResponse:
        """转换为响应模型（不含 api_key）"""
        return LLMConfigResponse(
            id=config["id"],
            base_url=config["base_url"],
            model=config["model"],
            temperature=config["temperature"],
            max_tokens=config["max_tokens"],
            timeout=config["timeout"],
            is_active=config["is_active"],
            has_api_key=True,
            created_at=config["created_at"],
            updated_at=config["updated_at"],
        )
