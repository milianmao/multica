"""
backend/result/canonical_result.py

规范结果构建器 - 组装 Canonical Result

对应数据流节点：O（Canonical Result）

职责：
1. 构建成功的规范结果
2. 构建 withheld 的规范结果
3. 构建 Provenance
4. 构建 limitations
"""

from __future__ import annotations

import platform
from typing import Any, Dict, List, Optional

from ai_quality.contracts.evaluation_contract import (
    AggregateStatus,
    CanonicalResult,
    ExecutionEnvironment,
    Finding,
    Profile,
    Provenance,
)


class CanonicalResultBuilder:
    """
    规范结果构建器

    组装 CanonicalResult，确保：
    - 非 scored 状态的 score 始终为 null
    - evaluator error 不转成 0.5
    - 缺失权重不归一化
    - withheld/failed/canceled/expired/inconclusive 不能渲染为成功评测
    """

    # 引擎版本信息
    ENGINE_VERSION = "0.2.2"
    SCHEMA_VERSION = "evaluation-contract-v1"
    RULESET_VERSION = "static-rules-0.2.2"
    PROFILE_VERSION = "0.2.2"

    def build(
        self,
        evaluation_id: str,
        requested_profile: Profile,
        effective_profile: Profile,
        aggregate_status: AggregateStatus,
        findings: List[Finding],
        evidence_manifest: List[Dict[str, Any]],
        artifact_digest: str,
        limitations: Optional[List[str]] = None,
    ) -> CanonicalResult:
        """
        构建成功的规范结果

        Args:
            evaluation_id: 评测 ID
            requested_profile: 请求的 Profile
            effective_profile: 实际执行的 Profile
            aggregate_status: 聚合状态
            findings: Finding 列表
            evidence_manifest: Evidence Manifest
            artifact_digest: Artifact digest
            limitations: 限制说明（可选）

        Returns:
            CanonicalResult
        """
        provenance = self._build_provenance(
            evaluation_id, effective_profile, artifact_digest
        )

        # 构建 limitations
        final_limitations = self._build_limitations(effective_profile)
        if limitations:
            final_limitations.extend(limitations)

        return CanonicalResult(
            evaluation_id=evaluation_id,
            requested_profile=requested_profile,
            effective_profile=effective_profile,
            claim_scope="code_snapshot",
            aggregate_status=aggregate_status,
            findings=findings,
            evidence_manifest=evidence_manifest,
            provenance=provenance,
            limitations=final_limitations,
        )

    def build_withheld(
        self,
        evaluation_id: str,
        requested_profile: Profile,
        effective_profile: Optional[Profile],
        reason: str,
        evidence_manifest: List[Dict[str, Any]],
        artifact_digest: str,
    ) -> CanonicalResult:
        """
        构建 withheld 的规范结果

        当 Profile 不可用时调用，返回 withheld 状态的结果。

        Args:
            evaluation_id: 评测 ID
            requested_profile: 请求的 Profile
            effective_profile: 实际执行的 Profile（可能为 None）
            reason: withheld 原因
            evidence_manifest: Evidence Manifest
            artifact_digest: Artifact digest

        Returns:
            CanonicalResult（withheld 状态）
        """
        # 如果 effective_profile 为 None，使用 CODE_STATIC 作为占权
        profile_for_provenance = effective_profile or Profile.CODE_STATIC

        provenance = self._build_provenance(
            evaluation_id, profile_for_provenance, artifact_digest
        )

        return CanonicalResult(
            evaluation_id=evaluation_id,
            requested_profile=requested_profile,
            effective_profile=profile_for_provenance,
            claim_scope="code_snapshot",
            aggregate_status=AggregateStatus.WITHHELD,
            findings=[],
            evidence_manifest=evidence_manifest,
            provenance=provenance,
            limitations=[reason],
        )

    def build_failed(
        self,
        evaluation_id: str,
        requested_profile: Profile,
        effective_profile: Optional[Profile],
        error_reason: str,
        evidence_manifest: List[Dict[str, Any]],
        artifact_digest: str,
        aggregate_status: AggregateStatus = AggregateStatus.FAILED,
    ) -> CanonicalResult:
        """
        构建失败的规范结果

        用于 failed/canceled/expired/inconclusive 等失败状态。
        这些状态不能渲染为成功评测。

        Args:
            evaluation_id: 评测 ID
            requested_profile: 请求的 Profile
            effective_profile: 实际执行的 Profile
            error_reason: 失败原因
            evidence_manifest: Evidence Manifest
            artifact_digest: Artifact digest
            aggregate_status: 聚合状态（FAILED/CANCELED/EXPIRED/INCONCLUSIVE）

        Returns:
            CanonicalResult（失败状态）
        """
        profile_for_provenance = effective_profile or Profile.CODE_STATIC

        provenance = self._build_provenance(
            evaluation_id, profile_for_provenance, artifact_digest
        )

        return CanonicalResult(
            evaluation_id=evaluation_id,
            requested_profile=requested_profile,
            effective_profile=profile_for_provenance,
            claim_scope="code_snapshot",
            aggregate_status=aggregate_status,
            findings=[],
            evidence_manifest=evidence_manifest,
            provenance=provenance,
            limitations=[error_reason],
        )

    def _build_provenance(
        self,
        evaluation_id: str,
        profile: Profile,
        artifact_digest: str,
    ) -> Provenance:
        """
        构建 Provenance

        记录评测如何产生、使用了哪些版本和环境。

        Args:
            evaluation_id: 评测 ID
            profile: Profile
            artifact_digest: Artifact digest

        Returns:
            Provenance
        """
        return Provenance(
            evaluation_id=evaluation_id,
            schema_version=self.SCHEMA_VERSION,
            profile_id=profile.value if profile else None,
            profile_version=self.PROFILE_VERSION,
            claim_scope="code_snapshot",
            engine_version=self.ENGINE_VERSION,
            ruleset_version=self.RULESET_VERSION,
            artifact_digest=artifact_digest,
            execution_environment=ExecutionEnvironment(
                id="local:evaluation-core",
                os=platform.system().lower(),
                runtime="python",
                runtime_version=platform.python_version(),
            ),
        )

    def _build_limitations(self, profile: Profile) -> List[str]:
        """
        构建 limitations

        根据 Profile 类型返回相应的限制说明。

        Args:
            profile: Profile

        Returns:
            限制说明列表
        """
        limitations: List[str] = []

        if profile == Profile.CODE_STATIC:
            limitations.extend([
                "v0.2 仅提供静态分析，不执行代码",
                "不提供总分、健康分、实测功能/性能分",
            ])

        return limitations
