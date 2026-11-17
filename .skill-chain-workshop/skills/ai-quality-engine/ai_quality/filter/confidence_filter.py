"""
backend/filter/confidence_filter.py

Confidence Filter - 置信度阈值过滤

对齐设计文档 §7.5、契约规范 §6.4，实现：
- 基于置信度阈值的 Finding 过滤
- 支持 filter（隐藏）和 dim（淡化）两种模式
- 阈值 = 0 时关闭过滤

对应数据流节点：L（Confidence Filter）
"""

from typing import Dict, List, Tuple

from ai_quality.contracts.evaluation_contract import DisplayStatus, Finding


class ConfidenceFilter:
    """
    置信度阈值过滤

    根据 Finding 的 confidence.value（0.0-1.0）与阈值（0-100）的比较，
    决定 Finding 的显示状态（visible、hidden、dimmed）。
    """

    def filter_findings(
        self,
        findings: List[Finding],
        threshold: float,  # 0-100
        action: str,  # "filter" or "dim"
    ) -> Tuple[List[Finding], Dict[str, int]]:
        """
        根据阈值过滤 Finding

        Args:
            findings: Finding 列表
            threshold: 置信度阈值（0-100），0 表示关闭过滤
            action: 过滤动作，"filter"=隐藏低于阈值的 Finding，"dim"=淡化显示

        Returns:
            Tuple[filtered_findings, stats]
            stats: {visible: int, hidden: int, dimmed: int}

        Raises:
            ValueError: 当 threshold 超出 0-100 范围或 action 无效时
        """
        # 验证参数
        if not 0 <= threshold <= 100:
            raise ValueError(
                f"confidence_threshold.value must be between 0 and 100, got {threshold}"
            )
        if action not in ("filter", "dim"):
            raise ValueError(
                f"confidence_threshold.action must be 'filter' or 'dim', got '{action}'"
            )

        # 阈值 = 0 时，关闭过滤，所有 Finding 都可见
        if threshold == 0:
            for finding in findings:
                finding.display_status = DisplayStatus.VISIBLE
            return findings, {"visible": len(findings), "hidden": 0, "dimmed": 0}

        filtered: List[Finding] = []
        stats = {"visible": 0, "hidden": 0, "dimmed": 0}

        for finding in findings:
            # 将 confidence.value（0.0-1.0）转换为 0-100 范围
            confidence_value = finding.confidence.value * 100

            if confidence_value >= threshold:
                # 高于阈值，保持可见
                finding.display_status = DisplayStatus.VISIBLE
                stats["visible"] += 1
                filtered.append(finding)
            else:
                # 低于阈值，根据 action 处理
                if action == "filter":
                    # 隐藏低于阈值的 Finding
                    finding.display_status = DisplayStatus.HIDDEN
                    stats["hidden"] += 1
                    # 不添加到 filtered 列表（从结果中移除）
                elif action == "dim":
                    # 淡化显示低于阈值的 Finding
                    finding.display_status = DisplayStatus.DIMMED
                    stats["dimmed"] += 1
                    filtered.append(finding)

        return filtered, stats
