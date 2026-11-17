"""
backend/comparator/__init__.py

改进对比模块

对应数据流节点：M（Improvement Comparator）
"""

from ai_quality.comparator.improvement_comparator import ImprovementComparator
from ai_quality.comparator.finding_matcher import FindingMatcher

__all__ = ["ImprovementComparator", "FindingMatcher"]
