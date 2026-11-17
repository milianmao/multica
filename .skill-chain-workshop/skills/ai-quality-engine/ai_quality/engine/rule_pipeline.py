"""按固定顺序组合确定性规则引擎。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Protocol

from ai_quality.contracts.evaluation_contract import Finding


class RuleEngine(Protocol):
    """规则引擎最小接口，便于注入和扩展。"""

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        raise NotImplementedError


class RulePipeline:
    """有序 Composite：聚合规则结果但不改变既有执行顺序。"""

    def __init__(self, engines: Iterable[RuleEngine]):
        self._engines = tuple(engines)

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        findings: List[Finding] = []
        for engine in self._engines:
            run_engine = engine.execute
            findings.extend(run_engine(normalized_artifacts, evidence_manifest))
        return findings
