"""
ai_quality/rules/common.py

规则引擎共享工具

消除各规则引擎中重复的 evidence_id 匹配逻辑。
"""

from __future__ import annotations

from typing import Any, Dict, List


def match_evidence_id(
    artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
) -> str:
    """
    根据 artifact path 精确匹配对应的 evidence_id

    多个规则引擎共用此逻辑：以 artifact["path"] 为键匹配 manifest 中的 evidence，
    匹配到则返回其 evidence_id；未匹配到则回退到第一条 evidence（与旧行为一致），
    极端情况下返回占位符。

    Args:
        artifact: 规范化后的 Artifact（含 path 字段）
        evidence_manifest: Evidence Manifest 列表

    Returns:
        匹配到的 evidence_id
    """
    artifact_path = artifact.get("path", "")

    if artifact_path:
        for evidence in evidence_manifest:
            if evidence.get("path") == artifact_path:
                return evidence["evidence_id"]

    # 兜底：未匹配到则回退第一条 evidence，避免空引用
    if evidence_manifest:
        return evidence_manifest[0]["evidence_id"]

    return "ev_unknown"
