"""
backend/cli/aggregator.py

ai-quality diagnose 健康分与聚合

对应数据流节点：B（CLI Adapter）
设计文档：v0.4 设计文档 §7

职责：
    aggregate(findings, project_path, top_n) -> ProjectReport
    按维度聚合严重度分布，计算健康分（0-100），输出 Top-N 问题清单。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

# 严重度扣分权重（设计文档 §7.2）
SEVERITY_WEIGHT = {
    "critical": 10.0,
    "error": 5.0,
    "warning": 2.0,
    "info": 0.5,
}

# 严重度排序权重（用于 Top-N 排序，越大越靠前）
_SEVERITY_RANK = {"critical": 4, "error": 3, "warning": 2, "info": 1}

# 维度多样性惩罚（设计文档 §7.2）
DIVERSITY_PENALTY_PER_DIMENSION = 3
DIVERSITY_FREE_DIMENSIONS = 2

# 设计文档 §8.2 展示的固定维度顺序
DIMENSION_ORDER = [
    "maintainability",
    "reliability",
    "security",
    "correctness",
    "readability",
    "performance",
    "complexity",
    "aislop",
    "mock",
    "test-quality",
]


@dataclass
class DimensionSummary:
    """单维度严重度分布。"""

    dimension: str
    total: int = 0
    critical: int = 0
    error: int = 0
    warning: int = 0
    info: int = 0


@dataclass
class ProjectReport:
    """项目级聚合报告。"""

    project_path: str
    health_score: float  # 0-100
    total_findings: int
    triggered_dimensions: int
    dimensions: List[DimensionSummary]
    top_n: List[dict]  # Top-N findings（序列化后的 dict）
    generated_at: str  # ISO8601


def _severity_value(finding) -> str:
    """取 finding 严重度字符串值。"""
    return getattr(finding.severity, "value", str(finding.severity))


def _confidence_value(finding) -> float:
    """取 finding 置信度（0-1）。"""
    confidence = getattr(finding, "confidence", None)
    value = getattr(confidence, "value", None)
    return float(value) if value is not None else 1.0


def _finding_sort_key(finding) -> float:
    """Top-N 排序键：severity 权重 × confidence（越大越靠前）。"""
    severity = _severity_value(finding)
    return _SEVERITY_RANK.get(severity, 1) * _confidence_value(finding)


def _finding_to_dict(finding) -> dict:
    """把 Finding 序列化为报告 Top-N 条目所需的 dict。"""
    source = getattr(finding, "source", None)
    return {
        "rule_id": finding.rule_id,
        "title": finding.title,
        "severity": _severity_value(finding),
        "confidence": _confidence_value(finding),
        "dimension": finding.dimension,
        "file": getattr(source, "file", None),
        "line_start": getattr(source, "line_start", None),
        "line_end": getattr(source, "line_end", None),
        "symptom": getattr(finding, "symptom", ""),
        "consequence": getattr(finding, "consequence", ""),
        "remedy": getattr(finding, "remedy", ""),
    }


def aggregate(findings, project_path: str, top_n: int = 10) -> ProjectReport:
    """把 CanonicalResult 的 findings 聚合为 ProjectReport。"""
    dims: Dict[str, DimensionSummary] = {}
    for finding in findings:
        dimension = getattr(finding, "dimension", None) or "unknown"
        summary = dims.setdefault(dimension, DimensionSummary(dimension=dimension))
        summary.total += 1
        severity = _severity_value(finding)
        if severity == "critical":
            summary.critical += 1
        elif severity == "error":
            summary.error += 1
        elif severity == "warning":
            summary.warning += 1
        else:
            summary.info += 1

    total = len(findings)
    triggered = len(dims)

    # 健康分 = 100 - Σ(severity_weight × confidence) - 维度多样性惩罚，clamp [0, 100]
    penalty = sum(SEVERITY_WEIGHT.get(_severity_value(f), 0.0) * _confidence_value(f) for f in findings)
    diversity_penalty = max(0, triggered - DIVERSITY_FREE_DIMENSIONS) * DIVERSITY_PENALTY_PER_DIMENSION
    health_score = round(max(0.0, min(100.0, 100.0 - penalty - diversity_penalty)), 1)

    # Top-N：按 severity×confidence 降序
    ordered = sorted(findings, key=_finding_sort_key, reverse=True)[:top_n]
    top_items = [_finding_to_dict(f) for f in ordered]

    # 维度排序：固定 7 维在前，未知维度追加在后
    ordered_dims = [dims[d] for d in DIMENSION_ORDER if d in dims] + [
        dims[d] for d in dims if d not in DIMENSION_ORDER
    ]

    return ProjectReport(
        project_path=project_path,
        health_score=health_score,
        total_findings=total,
        triggered_dimensions=triggered,
        dimensions=ordered_dims,
        top_n=top_items,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )
