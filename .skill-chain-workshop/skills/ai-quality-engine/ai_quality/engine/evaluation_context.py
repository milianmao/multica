"""EvaluationCore 的共享评测上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ai_quality.contracts.evaluation_contract import AggregateStatus, Profile


@dataclass
class EvaluationPreparation:
    """规则流水线执行前已经完成的规范化、证据和 Profile 门控结果。"""

    normalized_artifacts: List[Dict[str, Any]]
    evidence_manifest: List[Dict[str, Any]]
    artifact_digest: str
    requested_profile: Profile
    effective_profile: Profile
    aggregate_status: AggregateStatus
