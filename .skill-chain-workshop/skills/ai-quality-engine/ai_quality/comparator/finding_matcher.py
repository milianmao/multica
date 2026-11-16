"""
backend/comparator/finding_matcher.py

Finding Matcher - Finding 匹配算法

基于 ruleId + location + 文本相似度（Jaccard）的 Finding 匹配器，
用于匹配 baseline 和 current 评测中的 Finding。

对齐设计文档 §7.5，实现：
- ruleId 相同 +1 分
- location 相同（file + lineStart + lineEnd）+1 分
- 文本相似度（title + symptom）Jaccard 系数
- 综合相似度 >= 0.8 视为匹配
"""

from typing import Dict, List, Tuple

from ai_quality.contracts.evaluation_contract import Finding


class FindingMatcher:
    """
    Finding 匹配器 - 基于 ruleId + location + 文本相似度

    使用贪心算法匹配 baseline 和 current 的 Finding，
    每次选择相似度最高的匹配对。
    """

    def __init__(self, similarity_threshold: float = 0.8):
        """
        初始化 FindingMatcher

        Args:
            similarity_threshold: 相似度阈值，默认 0.8
        """
        self.similarity_threshold = similarity_threshold

    def match_findings(
        self,
        baseline_findings: List[Finding],
        current_findings: List[Finding],
    ) -> Dict[str, List]:
        """
        匹配 baseline 和 current 的 Finding

        使用贪心算法：遍历 baseline_findings，为每个 Finding 在
        current_findings 中寻找最佳匹配。

        Returns:
            {
                "matched": [(baseline_idx, current_idx), ...],
                "resolved": [baseline_idx, ...],
                "new": [current_idx, ...]
            }

        - matched: 匹配的 Finding 对（baseline 和 current 索引）
        - resolved: baseline 中有但 current 中没有匹配的 Finding（已修复）
        - new: current 中有但 baseline 中没有匹配的 Finding（新增）
        """
        matched: List[Tuple[int, int]] = []
        resolved: List[int] = []
        # 未匹配的 current Finding 索引集合
        unmatched_current = set(range(len(current_findings)))

        for b_idx, b_finding in enumerate(baseline_findings):
            best_match = None
            best_similarity = 0.0

            # 在未匹配的 current Finding 中查找最佳匹配
            for c_idx in unmatched_current:
                c_finding = current_findings[c_idx]
                similarity = self._compute_similarity(b_finding, c_finding)

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = c_idx

            # 如果最佳匹配的相似度 >= 阈值，则匹配成功
            if best_match is not None and best_similarity >= self.similarity_threshold:
                matched.append((b_idx, best_match))
                unmatched_current.remove(best_match)
            else:
                # 未找到匹配，标记为 resolved（已修复）
                resolved.append(b_idx)

        # 剩余的未匹配 current Finding 为 new（新增）
        new = list(unmatched_current)

        return {
            "matched": matched,
            "resolved": resolved,
            "new": new,
        }

    def _compute_similarity(
        self,
        finding_a: Finding,
        finding_b: Finding,
    ) -> float:
        """
        计算两个 Finding 的相似度

        评分规则：
        1. ruleId 相同 +1 分
        2. location 相同（file + lineStart + lineEnd）+1 分
        3. 文本相似度（title + symptom）Jaccard 系数（0-1）

        最终相似度 = 总分 / 最大分（3）

        Args:
            finding_a: Finding A
            finding_b: Finding B

        Returns:
            相似度值（0.0-1.0）
        """
        score = 0.0
        max_score = 3.0

        # ruleId 比较（权重 1）
        if finding_a.rule_id == finding_b.rule_id:
            score += 1.0

        # location 比较（权重 1）
        loc_a = finding_a.source
        loc_b = finding_b.source
        if (
            loc_a.file == loc_b.file
            and loc_a.line_start == loc_b.line_start
            and loc_a.line_end == loc_b.line_end
        ):
            score += 1.0

        # 文本相似度（Jaccard，权重 1）
        text_a = f"{finding_a.title} {finding_a.symptom}"
        text_b = f"{finding_b.title} {finding_b.symptom}"
        jaccard = self._jaccard_similarity(text_a, text_b)
        score += jaccard

        return score / max_score

    def _jaccard_similarity(self, text_a: str, text_b: str) -> float:
        """
        计算 Jaccard 相似度（基于词集合）

        Jaccard = |A ∩ B| / |A ∪ B|

        Args:
            text_a: 文本 A
            text_b: 文本 B

        Returns:
            Jaccard 相似度（0.0-1.0）
        """
        # 分词并转为小写
        set_a = set(text_a.lower().split())
        set_b = set(text_b.lower().split())

        # 两个空集合的相似度定义为 1.0
        if not set_a and not set_b:
            return 1.0

        intersection = set_a.intersection(set_b)
        union = set_a.union(set_b)

        return len(intersection) / len(union) if union else 0.0
