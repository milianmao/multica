"""
backend/engine/evaluation_core.py

评测核心引擎 - 唯一规则执行器

对应数据流节点：
- I（Static Rules）
- K（Finding Ledger）
- O（Canonical Result）

职责：
1. Artifact 规范化
2. Evidence Manifest 生成
3. Profile Gate 评估
4. 静态规则执行
5. Finding Ledger 去重排序
6. Canonical Result 组装

约束（REQ-CORE-04）：
- Core 接收契约请求后只运行通过 Gate 的评测器
- 所有 Finding 带 Evidence 引用
- Core 不产生总分、健康分和 Agent 三维
- 并发请求之间不存在模块级共享输入串扰
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Union

from ai_quality.contracts.evaluation_contract import (
    AggregateStatus,
    CanonicalResult,
    EvidenceRef,
    Profile,
)
from ai_quality.evidence.artifact_normalizer import ArtifactNormalizer
from ai_quality.evidence.evidence_manifest import EvidenceManifestGenerator
from ai_quality.profile.profile_gate import ProfileGate
from ai_quality.result.canonical_result import CanonicalResultBuilder
from ai_quality.result.finding_ledger import FindingLedger
from ai_quality.rules.static_rules import StaticRulesEngine
from ai_quality.rules.security_rules import SecurityRulesEngine
from ai_quality.rules.correctness_rules import CorrectnessRulesEngine
from ai_quality.rules.readability_rules import ReadabilityRulesEngine
from ai_quality.rules.performance_rules import PerformanceRulesEngine
from ai_quality.rules.complexity_rules import ComplexityRulesEngine
from ai_quality.rules.aislop_rules import AislopRulesEngine
from ai_quality.rules.mock_rules import MockRulesEngine
from ai_quality.rules.assertion_rules import AssertionRulesEngine
from ai_quality.engine.oracle_rules import OracleRulesEngine, OracleManifestError
from ai_quality.engine.evaluation_context import EvaluationPreparation
from ai_quality.engine.rule_pipeline import RulePipeline


class EvaluationCore:
    """
    评测核心引擎

    唯一规则执行器，负责：
    - 接收契约请求
    - 执行通过 Gate 的评测器
    - 返回 Canonical Result

    设计原则：
    - 无模块级共享状态（避免并发串扰）
    - 所有 Finding 带 Evidence 引用
    - 不产生总分、健康分和 Agent 三维
    """

    _RULE_ENGINE_TYPES = (
        ("static_rules", StaticRulesEngine),
        ("security_rules", SecurityRulesEngine),
        ("correctness_rules", CorrectnessRulesEngine),
        ("readability_rules", ReadabilityRulesEngine),
        ("performance_rules", PerformanceRulesEngine),
        ("complexity_rules", ComplexityRulesEngine),
        ("aislop_rules", AislopRulesEngine),
        ("mock_rules", MockRulesEngine),
        ("assertion_rules", AssertionRulesEngine),
    )

    def __init__(
        self,
        profile_gate: Optional[ProfileGate] = None,
        static_rules: Optional[StaticRulesEngine] = None,
        security_rules: Optional[SecurityRulesEngine] = None,
        correctness_rules: Optional[CorrectnessRulesEngine] = None,
        readability_rules: Optional[ReadabilityRulesEngine] = None,
        performance_rules: Optional[PerformanceRulesEngine] = None,
        complexity_rules: Optional[ComplexityRulesEngine] = None,
        aislop_rules: Optional[AislopRulesEngine] = None,
        mock_rules: Optional[MockRulesEngine] = None,
        assertion_rules: Optional[AssertionRulesEngine] = None,
        oracle_rules: Optional[OracleRulesEngine] = None,
        oracle_manifest_dir: Optional[str] = None,
        ledger: Optional[FindingLedger] = None,
        result_builder: Optional[CanonicalResultBuilder] = None,
    ):
        """初始化可注入依赖；未提供的组件使用默认实现。"""
        self.profile_gate = profile_gate or ProfileGate()
        self._init_rule_engines(
            {
                "static_rules": static_rules,
                "security_rules": security_rules,
                "correctness_rules": correctness_rules,
                "readability_rules": readability_rules,
                "performance_rules": performance_rules,
                "complexity_rules": complexity_rules,
                "aislop_rules": aislop_rules,
                "mock_rules": mock_rules,
                "assertion_rules": assertion_rules,
            }
        )
        self.oracle_manifest_dir = oracle_manifest_dir
        self.oracle_rules = oracle_rules or OracleRulesEngine(
            oracle_manifest_dir=oracle_manifest_dir
        )
        self.ledger = ledger or FindingLedger()
        self.result_builder = result_builder or CanonicalResultBuilder()
        self.rule_pipeline = RulePipeline(self._ordered_rule_engines())

    def _init_rule_engines(self, provided: Dict[str, Any]) -> None:
        """初始化可注入的规则引擎，集中维护默认实现。"""
        for name, engine_type in self._RULE_ENGINE_TYPES:
            setattr(self, name, provided.get(name) or engine_type())

    def _ordered_rule_engines(self) -> List[Any]:
        """按契约顺序返回规则引擎，避免入口重复维护顺序。"""
        return [getattr(self, name) for name, _ in self._RULE_ENGINE_TYPES]

    async def evaluate(
        self,
        artifacts: List[Dict[str, Any]],
        requested_profile: Profile,
        fallback_profile: Optional[Profile] = None,
        evidence_refs: Optional[List[EvidenceRef]] = None,
        baseline_evaluation_id: Optional[str] = None,
    ) -> CanonicalResult:
        """执行一次异步评测；准备阶段与同步入口共享。"""
        preparation = self._prepare_evaluation(
            artifacts, requested_profile, fallback_profile, evidence_refs
        )
        if isinstance(preparation, CanonicalResult):
            return preparation
        return self._execute_prepared(preparation)

    def _prepare_evaluation(
        self,
        artifacts: List[Dict[str, Any]],
        requested_profile: Profile,
        fallback_profile: Optional[Profile],
        evidence_refs: Optional[List[EvidenceRef]] = None,
    ) -> Union[CanonicalResult, EvaluationPreparation]:
        """共享 async/sync 入口的规范化、证据生成和 Profile 门控。"""
        normalized_artifacts = ArtifactNormalizer.normalize_artifacts(artifacts)
        artifact_digest = ArtifactNormalizer.compute_digest(artifacts)
        evidence_manifest = self._build_evidence_manifest(
            artifacts, artifact_digest, evidence_refs
        )

        effective_profile, aggregate_status, reason = self.profile_gate.evaluate_profile(
            requested_profile,
            [e["type"] for e in evidence_manifest],
            fallback_profile,
        )
        if aggregate_status == AggregateStatus.WITHHELD or effective_profile is None:
            return self.result_builder.build_withheld(
                evaluation_id=f"eval_{uuid.uuid4().hex[:12]}",
                requested_profile=requested_profile,
                effective_profile=effective_profile,
                reason=reason or "profile_withheld",
                evidence_manifest=evidence_manifest,
                artifact_digest=artifact_digest,
            )
        return EvaluationPreparation(
            normalized_artifacts=normalized_artifacts,
            evidence_manifest=evidence_manifest,
            artifact_digest=artifact_digest,
            requested_profile=requested_profile,
            effective_profile=effective_profile,
            aggregate_status=aggregate_status,
        )

    def _build_evidence_manifest(
        self,
        artifacts: List[Dict[str, Any]],
        artifact_digest: str,
        evidence_refs: Optional[List[EvidenceRef]],
    ) -> List[Dict[str, Any]]:
        """合并本地 Artifact、调用方引用和 Oracle 证据。"""
        manifest = EvidenceManifestGenerator.generate_manifest(artifacts, artifact_digest)
        if evidence_refs:
            manifest.extend(reference.model_dump(mode="json") for reference in evidence_refs)
        oracle_evidence = EvidenceManifestGenerator.generate_oracle_evidence(
            self.oracle_manifest_dir, artifact_digest
        )
        if oracle_evidence:
            manifest.extend(oracle_evidence)
        return manifest

    def _execute_prepared(self, preparation: EvaluationPreparation) -> CanonicalResult:
        """执行已经完成 Profile 门控的评测上下文。"""
        return self._execute_pipeline(preparation)

    def _execute_pipeline(self, preparation: EvaluationPreparation) -> CanonicalResult:
        """执行规则、Oracle、Ledger，并组装规范结果。"""
        findings = self.rule_pipeline.execute(
            preparation.normalized_artifacts, preparation.evidence_manifest
        )
        withheld = self._execute_oracle(preparation, findings)
        if withheld is not None:
            return withheld
        return self._build_result(preparation, findings)

    def _execute_oracle(
        self, preparation: EvaluationPreparation, findings: List[Any]
    ) -> Optional[CanonicalResult]:
        """执行 code-verified Oracle；非法 manifest 时 fail-closed。"""
        if preparation.effective_profile.value != "code-verified":
            return None
        try:
            findings.extend(
                self.oracle_rules.execute(
                    preparation.normalized_artifacts, preparation.evidence_manifest
                )
            )
        except OracleManifestError as exc:
            return self.result_builder.build_withheld(
                evaluation_id=f"eval_{uuid.uuid4().hex[:12]}",
                requested_profile=preparation.requested_profile,
                effective_profile=preparation.effective_profile,
                reason=str(exc),
                evidence_manifest=preparation.evidence_manifest,
                artifact_digest=preparation.artifact_digest,
            )
        return None

    def _build_result(
        self, preparation: EvaluationPreparation, findings: List[Any]
    ) -> CanonicalResult:
        """对 Finding 去重排序并组装 CanonicalResult。"""
        return self.result_builder.build(
            evaluation_id=f"eval_{uuid.uuid4().hex[:12]}",
            requested_profile=preparation.requested_profile,
            effective_profile=preparation.effective_profile,
            aggregate_status=preparation.aggregate_status,
            findings=self.ledger.deduplicate_and_sort(findings),
            evidence_manifest=preparation.evidence_manifest,
            artifact_digest=preparation.artifact_digest,
        )

    def evaluate_sync(
        self,
        artifacts: List[Dict[str, Any]],
        requested_profile: Profile,
        fallback_profile: Optional[Profile] = None,
        evidence_refs: Optional[List[EvidenceRef]] = None,
        baseline_evaluation_id: Optional[str] = None,
    ) -> CanonicalResult:
        """
        同步执行评测（便捷方法）

        与 evaluate 相同，但使用同步调用。

        Args:
            artifacts: Artifact 列表
            requested_profile: 请求的 Profile
            fallback_profile: 回退 Profile
            evidence_refs: 预提供的 Evidence 引用
            baseline_evaluation_id: 基线评测 ID

        Returns:
            CanonicalResult
        """
        preparation = self._prepare_evaluation(
            artifacts, requested_profile, fallback_profile, evidence_refs
        )
        if isinstance(preparation, CanonicalResult):
            return preparation
        return self._execute_prepared(preparation)
