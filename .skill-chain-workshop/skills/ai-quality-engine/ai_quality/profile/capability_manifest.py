"""
Capability Manifest 定义

根据 v0.2.2 契约规范 §3.3 实现，定义 Profile 和 Command 的能力清单。
"""
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from enum import Enum

from ai_quality import __version__


class ProfileStatus(str, Enum):
    """Profile 状态枚举"""
    ENABLED = "enabled"
    DISABLED = "disabled"


class ProfileInfo(BaseModel):
    """Profile 详细信息"""
    status: ProfileStatus
    allowed_claims: List[str] = []
    forbidden_claims: List[str] = []
    required_evidence: List[str] = []
    reason: Optional[str] = None


class CommandInfo(BaseModel):
    """Command 详细信息"""
    status: ProfileStatus
    options: List[str] = []
    reason: Optional[str] = None


class CapabilityManifest(BaseModel):
    """
    Capability Manifest 模型

    根据 v0.2.2 契约规范 §3.3，定义系统支持的 Profile 和 Command 能力。
    """
    schema_version: str = "evaluation-contract-v1"
    engine_version: str = Field(default_factory=lambda: __version__)
    profiles: Dict[str, ProfileInfo]
    commands: Dict[str, CommandInfo]

    @staticmethod
    def get_default_manifest() -> 'CapabilityManifest':
        """
        返回 v0.2.2 默认 Capability Manifest

        根据设计文档 §7.3 和契约规范 §3.3，定义以下能力：
        - code-static: 启用（需要 code 或 diff 证据）
        - code-verified: 禁用（需要沙箱和测试 Oracle）
        - agent-verified: 禁用（需要可信 Trace）
        """
        return CapabilityManifest(
            profiles={
                "code-static": ProfileInfo(
                    status=ProfileStatus.ENABLED,
                    allowed_claims=[
                        "static_findings",
                        "qualitative_guidance",
                        "improvement_comparison"
                    ],
                    forbidden_claims=[
                        "total_score",
                        "agent_capability"
                    ]
                ),
                "code-verified": ProfileInfo(
                    status=ProfileStatus.ENABLED,
                    allowed_claims=[
                        "contract_violation",
                        "scenario_failure",
                        "business_logic_deviation"
                    ],
                    required_evidence=["contract_oracle", "scenario_oracle"],
                    reason=None
                ),
                "agent-verified": ProfileInfo(
                    status=ProfileStatus.DISABLED,
                    required_evidence=["trusted_signed_trace", "artifact_binding"],
                    reason="trusted_trace_not_implemented"
                )
            },
            commands={
                "check": CommandInfo(
                    status=ProfileStatus.ENABLED,
                    options=[
                        "--baseline",
                        "--min-confidence",
                        "--low-confidence-action"
                    ]
                ),
                "capabilities": CommandInfo(status=ProfileStatus.ENABLED),
                "review": CommandInfo(
                    status=ProfileStatus.DISABLED,
                    reason="not_in_v0.2_contract"
                ),
                "diagnose": CommandInfo(
                    status=ProfileStatus.ENABLED,
                    options=[
                        "--format",
                        "--top-n"
                    ]
                ),
                "compare": CommandInfo(
                    status=ProfileStatus.DISABLED,
                    reason="not_in_v0.2_contract"
                ),
                "health": CommandInfo(
                    status=ProfileStatus.ENABLED
                )
            }
        )
