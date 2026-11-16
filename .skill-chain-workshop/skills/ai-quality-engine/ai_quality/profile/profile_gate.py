"""
Profile Gate 逻辑

根据设计文档 §6 和 §7.3 实现，根据 Evidence 决定 effectiveProfile。
这是数据流节点 H（Profile Gate）的实现。
"""
from typing import List, Dict, Any, Optional, Tuple
from ai_quality.contracts.evaluation_contract import Profile, AggregateStatus
from ai_quality.profile.capability_manifest import CapabilityManifest, ProfileStatus


class ProfileGate:
    """
    Profile Gate - 根据 Evidence 决定 effectiveProfile

    职责：
    1. 检查 requested_profile 在 Capability Manifest 中的状态
    2. 验证是否有足够的 Evidence 支持该 Profile
    3. 返回 effectiveProfile、aggregateStatus 和 reason

    设计原则（来自设计文档 §2）：
    - Fail-closed: 缺证据时减少声明，不增加猜测
    - No hidden fallback: 更高 Profile 不可用时，只有用户明确请求 fallback 才能额外执行低 Profile
    """

    def __init__(self, manifest: Optional[CapabilityManifest] = None):
        """
        初始化 Profile Gate

        Args:
            manifest: Capability Manifest，如果为 None 则使用默认 Manifest
        """
        self.manifest = manifest or CapabilityManifest.get_default_manifest()

    def evaluate_profile(
        self,
        requested_profile: Profile,
        evidence_types: List[str],
        fallback_profile: Optional[Profile] = None
    ) -> Tuple[Optional[Profile], AggregateStatus, Optional[str]]:
        """
        评估请求的 Profile 是否可用

        根据设计文档 §6 和契约规范 §3.2 实现 Profile Gate 逻辑：
        1. 检查 Profile 是否存在于 Manifest
        2. 检查 Profile 是否启用
        3. 检查是否有足够的 Evidence

        Args:
            requested_profile: 请求的 Profile
            evidence_types: 已收集的 Evidence 类型列表
            fallback_profile: 显式指定的 fallback Profile（可选）

        Returns:
            Tuple[effectiveProfile, aggregateStatus, reason]
            - effectiveProfile: 有效的 Profile，如果不可用则为 None
            - aggregateStatus: 聚合状态
            - reason: 状态原因，如果成功则为 None
        """
        profile_info = self.manifest.profiles.get(requested_profile.value)

        # 检查 Profile 是否存在于 Manifest
        if not profile_info:
            return (
                None,
                AggregateStatus.FAILED,
                f"unknown_profile: {requested_profile}"
            )

        # 检查 Profile 是否禁用
        if profile_info.status == ProfileStatus.DISABLED:
            return (
                None,
                AggregateStatus.WITHHELD,
                profile_info.reason or "profile_disabled"
            )

        # code-static 特殊逻辑：检查是否有 code 或 diff Evidence
        if requested_profile == Profile.CODE_STATIC:
            return self._evaluate_code_static(evidence_types, fallback_profile)

        # code-verified 特殊逻辑：检查是否有 contract + scenario Oracle Evidence
        if requested_profile == Profile.CODE_VERIFIED:
            return self._evaluate_code_verified(evidence_types, fallback_profile)

        # 其他 Profile（理论上不会到这里，因为已经检查 disabled）
        return (
            None,
            AggregateStatus.WITHHELD,
            "unsupported_profile"
        )

    def _evaluate_code_static(
        self,
        evidence_types: List[str],
        fallback_profile: Optional[Profile]
    ) -> Tuple[Optional[Profile], AggregateStatus, Optional[str]]:
        """
        评估 code-static Profile

        根据契约规范 §3.2，code-static 需要 code 或 diff Evidence。

        Args:
            evidence_types: 已收集的 Evidence 类型列表
            fallback_profile: 显式指定的 fallback Profile

        Returns:
            Tuple[effectiveProfile, aggregateStatus, reason]
        """
        # 检查是否有 code 或 diff Evidence
        has_code_evidence = any(
            e in evidence_types for e in ["code", "diff"]
        )

        if not has_code_evidence:
            # 无代码证据，即使有显式 fallback 也无法执行
            return (
                None,
                AggregateStatus.WITHHELD,
                "insufficient_evidence: missing code or diff"
            )

        # 有代码证据，code-static 可用
        return (
            Profile.CODE_STATIC,
            AggregateStatus.VALID,
            None
        )

    def _evaluate_code_verified(
        self,
        evidence_types: List[str],
        fallback_profile: Optional[Profile]
    ) -> Tuple[Optional[Profile], AggregateStatus, Optional[str]]:
        """
        评估 code-verified Profile

        根据 capability_manifest.py，code-verified 需要 contract_oracle 与
        scenario_oracle 两类 evidence 同时 present。缺任一即 Fail-closed：
        WITHHELD（不降级到 code-static，除非调用方显式给出 fallback）。

        Args:
            evidence_types: 已收集的 Evidence 类型列表
            fallback_profile: 显式指定的 fallback Profile

        Returns:
            Tuple[effectiveProfile, aggregateStatus, reason]
        """
        has_contract = "contract_oracle" in evidence_types
        has_scenario = "scenario_oracle" in evidence_types

        if not (has_contract and has_scenario):
            # Fail-closed：缺任一 Oracle evidence 即扣留，不猜测、不补分。
            # 仅当调用方显式提供 fallback 时才降级（遵循 no hidden fallback 原则）。
            if fallback_profile is not None:
                return (
                    fallback_profile,
                    AggregateStatus.PROVISIONAL,
                    "missing_oracle_evidence: fallback to " + fallback_profile.value,
                )
            return (
                None,
                AggregateStatus.WITHHELD,
                "missing_oracle_evidence: need contract_oracle and scenario_oracle",
            )

        return (
            Profile.CODE_VERIFIED,
            AggregateStatus.VALID,
            None
        )

    def get_capability_manifest(self) -> Dict[str, Any]:
        """
        返回 Capability Manifest（JSON 序列化）

        根据契约规范 §3.3，用于 GET /api/v1/capabilities 端点。

        Returns:
            Dict[str, Any]: Capability Manifest 的字典表示
        """
        return self.manifest.model_dump()
