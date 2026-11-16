"""
backend/result/finding_ledger.py

Finding 账本 - 去重和排序

对应数据流节点：K（Finding Ledger）

职责：
1. 去重：ruleId + file + lineStart 相同视为重复
2. 排序：severity (critical > error > warning > info) → confidence (高到低)
"""

from __future__ import annotations

from collections import OrderedDict
from typing import List

from ai_quality.contracts.evaluation_contract import Finding, Severity


class FindingLedger:
    """
    Finding 账本

    负责 Finding 的去重和排序，确保：
    - 同一规则在同一位置不重复报告
    - Finding 按严重度和置信度有序展示
    """

    # 严重度排序权重（越小越严重）
    SEVERITY_ORDER = {
        Severity.CRITICAL: 0,
        Severity.ERROR: 1,
        Severity.WARNING: 2,
        Severity.INFO: 3,
    }

    def deduplicate_and_sort(self, findings: List[Finding]) -> List[Finding]:
        """
        去重和排序 Finding 列表

        流程：
        1. 去重：ruleId + file + lineStart
        2. 排序：severity (critical > error > warning > info) → confidence (高到低)

        Args:
            findings: 原始 Finding 列表

        Returns:
            去重排序后的 Finding 列表
        """
        # 去重
        unique_findings = self._deduplicate(findings)

        # 排序
        sorted_findings = self._sort(unique_findings)

        return sorted_findings

    def _deduplicate(self, findings: List[Finding]) -> List[Finding]:
        """
        去重：ruleId + file + lineStart

        使用 OrderedDict 保持首次出现的顺序，后续重复项被忽略。

        Args:
            findings: Finding 列表

        Returns:
            去重后的 Finding 列表
        """
        seen: "OrderedDict[tuple, Finding]" = OrderedDict()

        for finding in findings:
            key = (
                finding.rule_id,
                finding.source.file,
                finding.source.line_start,
            )
            if key not in seen:
                seen[key] = finding

        return list(seen.values())

    def _sort(self, findings: List[Finding]) -> List[Finding]:
        """
        排序：severity → confidence

        排序规则：
        1. 严重度：critical > error > warning > info
        2. 置信度：高到低（降序）

        Args:
            findings: Finding 列表

        Returns:
            排序后的 Finding 列表
        """
        sorted_findings = sorted(
            findings,
            key=lambda f: (
                self.SEVERITY_ORDER.get(f.severity, 99),
                -f.confidence.value,  # 负号实现降序
            ),
        )

        return sorted_findings
