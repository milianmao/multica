"""
backend/comparator/improvement_comparator.py

Improvement Comparator - 改进对比器

对齐设计文档 §7.5、契约规范 §6.4，实现：
- Finding 分类：resolved（已修复）、new（新增）、unchanged（未变化）、regressed（退化）
- 计算 netImprovement = totalResolved - totalNew - totalRegressed
- 检测 artifactChanged

对应数据流节点：M（Improvement Comparator）
"""

from typing import Any, Dict, List, Optional

from ai_quality.contracts.evaluation_contract import Finding
from ai_quality.comparator.finding_matcher import FindingMatcher


class ImprovementComparator:
    """
    改进对比器

    对比 baseline 和 current 的 Finding，识别：
    - resolved: baseline 中有但 current 中没有（已修复）
    - new: current 中有但 baseline 中没有（新增）
    - unchanged: baseline 和 current 中都存在（未变化）
    - regressed: baseline 已修复但 current 又出现（退化）
    """

    def __init__(self, similarity_threshold: float = 0.8):
        """
        初始化 ImprovementComparator

        Args:
            similarity_threshold: Finding 匹配相似度阈值，默认 0.8
        """
        self.matcher = FindingMatcher(similarity_threshold)

    def compare(
        self,
        baseline_findings: List[Finding],
        current_findings: List[Finding],
        baseline_artifact_digest: Optional[str] = None,
        current_artifact_digest: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        对比 baseline 和 current 的 Finding

        Args:
            baseline_findings: baseline 评测的 Finding 列表
            current_findings: current 评测的 Finding 列表
            baseline_artifact_digest: baseline 的 artifact digest
            current_artifact_digest: current 的 artifact digest

        Returns:
            {
                "artifactChanged": bool,
                "deltaFindings": {
                    "resolved": [finding_id, ...],
                    "new": [finding_id, ...],
                    "unchanged": [finding_id, ...],
                    "regressed": [finding_id, ...]
                },
                "summary": {
                    "totalResolved": int,
                    "totalNew": int,
                    "totalUnchanged": int,
                    "totalRegressed": int,
                    "netImprovement": int
                }
            }

        - artifactChanged: artifact digest 是否变化
        - deltaFindings: Finding 变化分类（使用 finding_id）
        - summary: 汇总统计
        """
        # 匹配 Finding
        match_result = self.matcher.match_findings(baseline_findings, current_findings)

        # 分类 Finding
        resolved_ids = [
            baseline_findings[b_idx].finding_id for b_idx in match_result["resolved"]
        ]
        new_ids = [
            current_findings[c_idx].finding_id for c_idx in match_result["new"]
        ]
        unchanged_ids = [
            current_findings[c_idx].finding_id for _, c_idx in match_result["matched"]
        ]

        # regressed 逻辑：baseline 已修复（resolved）、当前又出现（new）
        # 简化实现：regressed = 空（需要跨多次评测才能准确判断）
        regressed_ids: List[str] = []

        # 计算汇总统计
        total_resolved = len(resolved_ids)
        total_new = len(new_ids)
        total_unchanged = len(unchanged_ids)
        total_regressed = len(regressed_ids)

        # netImprovement = totalResolved - totalNew - totalRegressed
        net_improvement = total_resolved - total_new - total_regressed

        return {
            "artifactChanged": baseline_artifact_digest != current_artifact_digest,
            "deltaFindings": {
                "resolved": resolved_ids,
                "new": new_ids,
                "unchanged": unchanged_ids,
                "regressed": regressed_ids,
            },
            "summary": {
                "totalResolved": total_resolved,
                "totalNew": total_new,
                "totalUnchanged": total_unchanged,
                "totalRegressed": total_regressed,
                "netImprovement": net_improvement,
            },
        }
