"""
backend/engine/oracle_rules.py

Oracle 规则引擎 —— 把 v0.7 Oracle Skill 产出的 manifest 接入 EvaluationCore 运行时。

痛点背景：contract-oracle-extractor / scenario-oracle-builder 已能离线把 SDD 契约 /
KB 业务流转化为"契约测试 + 场景测试 + manifest 覆盖率清单"，但这些产出此前只是
静态文件，没有任何引擎消费它们——code-verified Profile 一直处于"产出但不接线"的
悬空状态。本引擎读取这两个 manifest，把其中的缺口（gap）与业务风险转成 finding，
真正让 code-verified 的业务正确性断言进入评测链路。

对应数据流节点：I（Static Rules - code-verified Oracle）

消费的 manifest：
- oracle-manifest.json   (schema_version=contract-oracle-v1，contract-oracle-extractor 产出)
- scenario-manifest.json (schema_version=scenario-oracle-v1，scenario-oracle-builder 产出)

mapping（与 capability_manifest.py code-verified allowed_claims 严格对齐）：
- contract gap 端点        → contract_violation（契约违约）
- scenario gap 场景        → scenario_failure（场景未覆盖）
- mock_ceiling_risk 标志   → business_logic_deviation（Mock 天花板风险）

策略：
- 确定性、零 LLM。
- fail-closed：manifest 缺失或 JSON 非法时调用方返回 missing_oracle_manifest（不猜测）。
  （本引擎只处理"文件存在但缺口"，"文件不存在"由 EvaluationCore 提前按 fail-closed 处理）
- 每条 gap finding 带可追溯的 manifest 章节引用（sdd_section / flow 定位）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional
from pathlib import Path

from ai_quality.contracts.evaluation_contract import (
    Confidence,
    ConfidenceKind,
    Finding,
    FindingSource,
    Severity,
)

# 断言业务风险的确定性前提条件（决定性非启发式校验）
_CONFIDENCE_CALIBRATED = (
    Confidence(value=0.90, kind=ConfidenceKind.CALIBRATED),
)

# manifest 文件定位
CONTRACT_MANIFEST_FILENAME = "oracle-manifest.json"
SCENARIO_MANIFEST_FILENAME = "scenario-manifest.json"
PYTEST_RESULTS_FILENAME = "pytest-results.json"
CONTRACT_SCHEMA_VERSION = "contract-oracle-v1"
SCENARIO_SCHEMA_VERSION = "scenario-oracle-v1"


class OracleManifestError(Exception):
    """Oracle manifest 缺失或非法（fail-closed 信号）。"""


class OracleRulesEngine:
    """
    Oracle 规则引擎

    解析 contract/scenario oracle manifest，产出 code-verified 维度的 finding。
    确定性、零 LLM；失败 Fail-closed。
    """

    def __init__(
        self,
        oracle_manifest_dir: Optional[str] = None,
    ) -> None:
        """
        初始化 Oracle 规则引擎

        Args:
            oracle_manifest_dir: 存放 oracle-manifest.json + scenario-manifest.json 的目录。
                为 None 时不消费任何 manifest（相当于无 oracle 接线的 code-static 行为）。
        """
        self.oracle_manifest_dir = oracle_manifest_dir

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行 Oracle manifest 规则

        Args:
            normalized_artifacts: 规范化后的 Artifact 列表（本引擎不使用，签名与其他引擎对齐）
            evidence_manifest: Evidence Manifest（不使用）

        Returns:
            Finding 列表（contract_violation / scenario_failure / business_logic_deviation）
        """
        if not self.oracle_manifest_dir:
            return []

        root = Path(self.oracle_manifest_dir)
        contract, scenario = self._load_manifests(root)

        findings: List[Finding] = []
        if contract is not None:
            findings.extend(self._check_contract_manifest(contract, root))
        if scenario is not None:
            findings.extend(self._check_scenario_manifest(scenario, root))
        findings.extend(self._check_pytest_results(root))
        return findings

    # ═══════════════════════════════════════════════════════════════
    # manifest 加载（Fail-closed）
    # ═══════════════════════════════════════════════════════════════

    def load_contract_manifest(self) -> Optional[Dict[str, Any]]:
        """加载契约 manifest；文件不存在返回 None，存在但非法抛异常（fail-closed）。"""
        if not self.oracle_manifest_dir:
            return None
        return self._load_single(Path(self.oracle_manifest_dir), CONTRACT_MANIFEST_FILENAME)

    def load_scenario_manifest(self) -> Optional[Dict[str, Any]]:
        """加载场景 manifest；文件不存在返回 None，存在但非法抛异常（fail-closed）。"""
        if not self.oracle_manifest_dir:
            return None
        return self._load_single(Path(self.oracle_manifest_dir), SCENARIO_MANIFEST_FILENAME)

    @staticmethod
    def _load_single(root: Path, filename: str) -> Optional[Dict[str, Any]]:
        """读取并校验单个 manifest；不存在返回 None，存在但非法则 fail-closed。"""
        path = root / filename
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise OracleManifestError(
                f"invalid_manifest: {filename}: {e}"
            ) from e
        OracleRulesEngine._validate_manifest(data, filename)
        return data

    @classmethod
    def _load_manifests(
        cls, root: Path
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """加载两个 manifest；任一存在但结构非法即 fail-closed。"""
        return (
            cls._load_single(root, CONTRACT_MANIFEST_FILENAME),
            cls._load_single(root, SCENARIO_MANIFEST_FILENAME),
        )

    @staticmethod
    def _validate_manifest(data: Any, filename: str) -> None:
        """拒绝空、错 schema 或不完整的 Oracle manifest。"""
        if not isinstance(data, dict):
            raise OracleManifestError(f"invalid_manifest: {filename}: expected object")
        if not data:
            raise OracleManifestError(f"invalid_manifest: {filename}: empty_manifest")

        # 纯前端/纯后端：契约维度显式声明跳过（declared_skipped），不要求 contracts/coverage。
        if (
            filename == CONTRACT_MANIFEST_FILENAME
            and data.get("declared_skipped") is True
        ):
            if data.get("schema_version") != CONTRACT_SCHEMA_VERSION:
                raise OracleManifestError(
                    f"invalid_manifest: {filename}: unsupported schema_version"
                )
            return

        if filename == CONTRACT_MANIFEST_FILENAME:
            schema = CONTRACT_SCHEMA_VERSION
            entries_key = "contracts"
            accepted_statuses = {"covered", "gap"}
        else:
            schema = SCENARIO_SCHEMA_VERSION
            entries_key = "flows" if "flows" in data else "scenarios"
            accepted_statuses = {"covered", "gap", "partial_parse", "unparseable"}

        if data.get("schema_version") != schema:
            raise OracleManifestError(
                f"invalid_manifest: {filename}: unsupported schema_version"
            )
        if filename == CONTRACT_MANIFEST_FILENAME and data.get("declared_skipped"):
            return
        entries = data.get(entries_key)
        if not isinstance(entries, list) or not entries:
            raise OracleManifestError(
                f"invalid_manifest: {filename}: missing_or_empty_{entries_key}"
            )
        coverage = data.get("coverage")
        if not isinstance(coverage, dict) or not isinstance(coverage.get("ratio"), (int, float)):
            raise OracleManifestError(
                f"invalid_manifest: {filename}: missing_coverage_ratio"
            )
        if not 0 <= coverage["ratio"] <= 1:
            raise OracleManifestError(
                f"invalid_manifest: {filename}: invalid_coverage_ratio"
            )
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("status") not in accepted_statuses:
                raise OracleManifestError(
                    f"invalid_manifest: {filename}: invalid_entry_status"
                )

    # ═══════════════════════════════════════════════════════════════
    # 契约 manifest 检查（contract_violation）
    # ═══════════════════════════════════════════════════════════════

    def _check_contract_manifest(
        self, manifest: Dict[str, Any], root: Path
    ) -> List[Finding]:
        """契约 manifest 中 status=gap 的端点逐个产生 contract_violation finding。"""
        if manifest.get("declared_skipped"):
            return []
        findings: List[Finding] = []
        if manifest.get("declared_skipped") is True:
            # 纯前端/纯后端：契约维度显式声明跳过，不产生 O-CONTRACT-GAP。
            return findings
        contracts = manifest.get("contracts") or []
        for entry in contracts:
            if entry.get("status") != "gap":
                continue
            findings.append(
                self._build_contract_finding(entry, manifest)
            )
        if manifest["coverage"]["ratio"] < 0.80:
            findings.append(self._build_contract_coverage_finding(manifest))
        return findings

    def _build_contract_coverage_finding(self, manifest: Dict[str, Any]) -> Finding:
        """构造一条契约覆盖率不足 finding。"""
        ratio = manifest["coverage"]["ratio"]
        source_file = manifest.get("source_sdd") or "oracle-manifest.json"
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="test-quality",
            rule_id="O-CONTRACT-COVERAGE-LOW",
            severity=Severity.WARNING,
            confidence=_CONFIDENCE_CALIBRATED[0],
            title="契约覆盖不足：SDD 端点覆盖率低于门禁阈值",
            symptom=f"契约 manifest 覆盖率为 {ratio:.2%}，低于 code-verified 所需的 80.00%",
            source=FindingSource(
                evidence_id="oracle_manifest",
                file=source_file,
                line_start=1,
                line_end=1,
            ),
            consequence="未被覆盖的 SDD 契约可能在代码变更后静默偏离，不能作为可靠业务验证证据",
            remedy="补齐缺失端点的契约测试并重跑 contract-oracle-extractor，使 coverage.ratio 至少为 0.80",
        )

    def _build_contract_finding(
        self, entry: Dict[str, Any], manifest: Dict[str, Any]
    ) -> Finding:
        """构造一条契约违约 finding（claim: contract_violation）。"""
        endpoint = entry.get("endpoint") or entry.get("path") or entry.get("method", "?") + " ?"
        section = entry.get("sdd_section") or "?"
        gap_reason = entry.get("gap_reason") or "gap"
        source_file = manifest.get("source_sdd") or "oracle-manifest.json"
        line_start = 1

        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="test-quality",
            rule_id="O-CONTRACT-GAP",
            severity=Severity.WARNING,
            confidence=_CONFIDENCE_CALIBRATED[0],
            title="契约违约：SDD 契约未覆盖端点",
            symptom=(
                f"契约 manifest 标记端点 '{endpoint}' 为 gap（{gap_reason}），"
                f"契约章节 {section} 未生成对应契约测试/覆盖率"
            ),
            source=FindingSource(
                evidence_id="oracle_manifest",
                file=source_file,
                line_start=line_start,
                line_end=line_start,
            ),
            consequence="契约列出的端点没有被测试覆盖，业务契约 (客户端/服务端一致协议) 缺乏验证，回归可能静默通过",
            remedy="补齐该端点的契约测试（contract-oracle-extractor 重跑）或显式标注排除理由，使 coverage.ratio ≥ 0.80",
        )

    # ═══════════════════════════════════════════════════════════════
    # 场景 manifest 检查（scenario_failure + business_logic_deviation）
    # ═══════════════════════════════════════════════════════════════

    def _check_scenario_manifest(
        self, manifest: Dict[str, Any], root: Path
    ) -> List[Finding]:
        """场景 manifest 的 gap 场景 + mock_ceiling_risk 标志转为 finding。

        🆕 2026-08-12 协议修正（schema drift 治本）：
        - 字段名：scenario-oracle-builder v0.7.0 写 `flows` 键（此前误读 `scenarios`
          → 永远读不到条目 → O-SCENARIO-GAP 永不触发 → scenario 假绿）
        - 状态值：builder 写 `covered/partial_parse/unparseable`（此前误判 `== "gap"`
          → partial/unparseable 流不报缺口）——现改为「仅 covered 视为覆盖，其余皆缺口」fail-closed
        """
        findings: List[Finding] = []
        scenarios = manifest.get("flows") or manifest.get("scenarios") or []
        for entry in scenarios:
            if entry.get("status") == "covered":
                continue
            findings.append(self._build_scenario_finding(entry, manifest))

        if manifest.get("mock_ceiling_risk"):
            findings.append(self._build_mock_ceiling_finding(manifest))
        if manifest["coverage"]["ratio"] < 0.90:
            findings.append(self._build_scenario_coverage_finding(manifest))
        return findings

    def _build_scenario_coverage_finding(self, manifest: Dict[str, Any]) -> Finding:
        """构造一条业务流覆盖率不足 finding。"""
        ratio = manifest["coverage"]["ratio"]
        source_file = manifest.get("source_kb") or "scenario-manifest.json"
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="test-quality",
            rule_id="O-SCENARIO-COVERAGE-LOW",
            severity=Severity.WARNING,
            confidence=_CONFIDENCE_CALIBRATED[0],
            title="场景覆盖不足：核心业务流覆盖率低于门禁阈值",
            symptom=f"场景 manifest 覆盖率为 {ratio:.2%}，低于 code-verified 所需的 90.00%",
            source=FindingSource(
                evidence_id="scenario_oracle",
                file=source_file,
                line_start=1,
                line_end=1,
            ),
            consequence="关键业务流缺乏当前代码的验证，回归可能在真实状态迁移或异常路径上静默失败",
            remedy="补齐业务流场景测试并重跑 scenario-oracle-builder，使 coverage.ratio 至少为 0.90",
        )

    def _build_scenario_finding(
        self, entry: Dict[str, Any], manifest: Dict[str, Any]
    ) -> Finding:
        """构造一条场景未覆盖 finding（claim: scenario_failure）。"""
        flow = entry.get("flow_name") or entry.get("flow") or entry.get("name") or "?"
        section = entry.get("kb_doc") or entry.get("flow_section") or entry.get("section") or "?"
        gap_reason = entry.get("status") or entry.get("gap_reason") or "gap"
        source_file = manifest.get("source_kb") or "scenario-manifest.json"

        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="test-quality",
            rule_id="O-SCENARIO-GAP",
            severity=Severity.WARNING,
            confidence=_CONFIDENCE_CALIBRATED[0],
            title="场景违约：核心业务流未覆盖",
            symptom=(
                f"场景 manifest 标记业务流 '{flow}' 为 gap（{gap_reason}），"
                f"业务流章节 {section} 未生成对应场景测试"
            ),
            source=FindingSource(
                evidence_id="scenario_oracle",
                file=source_file,
                line_start=1,
                line_end=1,
            ),
            consequence="核心业务流缺乏端到端覆盖，业务逻辑正确性得不到验证，回归可能在真实流程上静默失败",
            remedy="补齐该业务流的场景测试（scenario-oracle-builder 重跑）或显式标注排除，使核心流覆盖率 ≥ 0.90",
        )

    def _check_pytest_results(self, root: Path) -> List[Finding]:
        """把结构化 pytest 汇总中的失败转为业务门禁 finding。"""
        path = root / PYTEST_RESULTS_FILENAME
        if not path.is_file():
            return []
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise OracleManifestError(
                f"invalid_test_evidence: {PYTEST_RESULTS_FILENAME}: {e}"
            ) from e
        if not isinstance(result, dict):
            raise OracleManifestError(
                f"invalid_test_evidence: {PYTEST_RESULTS_FILENAME}: expected object"
            )
        summary = result.get("summary")
        if not isinstance(summary, dict) or not isinstance(summary.get("failed"), int):
            raise OracleManifestError(
                f"invalid_test_evidence: {PYTEST_RESULTS_FILENAME}: missing_failed_summary"
            )
        if result.get("exit_code") not in (0, 1):
            raise OracleManifestError(
                f"invalid_test_evidence: {PYTEST_RESULTS_FILENAME}: invalid_exit_code"
            )
        if result["exit_code"] == 0 and summary["failed"] != 0:
            raise OracleManifestError(
                f"invalid_test_evidence: {PYTEST_RESULTS_FILENAME}: inconsistent_summary"
            )
        if summary["failed"] == 0 and result["exit_code"] == 0:
            return []
        return [self._build_test_failure_finding(summary, path)]

    def _build_test_failure_finding(
        self, summary: Dict[str, Any], path: Path
    ) -> Finding:
        """构造当前 Oracle 测试运行失败 finding。"""
        failed = summary["failed"]
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="test-quality",
            rule_id="O-TEST-FAILURE",
            severity=Severity.WARNING,
            confidence=_CONFIDENCE_CALIBRATED[0],
            title="Oracle 测试失败：业务验证未通过",
            symptom=f"pytest evidence 记录 {failed} 个失败用例，当前 Oracle 不能作为通过证据",
            source=FindingSource(
                evidence_id="test_result",
                file=str(path),
                line_start=1,
                line_end=1,
            ),
            consequence="即使 manifest 声称覆盖，已失败的业务验证也表明当前实现不满足其测试断言",
            remedy="修复失败用例对应的业务偏离，重新运行统一业务质量门禁并生成新的 pytest-results.json",
        )

    def _build_mock_ceiling_finding(
        self, manifest: Dict[str, Any]
    ) -> Finding:
        """构造一条 Mock 天花板风险 finding（claim: business_logic_deviation）。"""
        source_file = manifest.get("source_kb") or "scenario-manifest.json"
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="test-quality",
            rule_id="O-MOCK-CEILING",
            severity=Severity.WARNING,
            confidence=_CONFIDENCE_CALIBRATED[0],
            title="业务逻辑偏离风险：Mock 天花板",
            symptom="场景 manifest 标记 mock_ceiling_risk=true——测试依赖 Mock 覆盖真实业务逻辑，存在被 Mock 掩盖的真实行为偏离风险",
            source=FindingSource(
                evidence_id="scenario_oracle",
                file=source_file,
                line_start=1,
                line_end=1,
            ),
            consequence="测试在 Mock 层通过但真实组件聚合后行为可能偏离，业务正确性断言可信度下降",
            remedy="用真实组件/集成环境替代关键 Mock，或补充真实端到端场景测试消除 Mock 天花板",
        )
