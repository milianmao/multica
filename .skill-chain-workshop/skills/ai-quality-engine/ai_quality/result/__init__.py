"""
backend/result/__init__.py

结果处理模块 - Finding 去重排序与 Canonical Result 组装
"""

from ai_quality.result.finding_ledger import FindingLedger
from ai_quality.result.canonical_result import CanonicalResultBuilder

__all__ = ["FindingLedger", "CanonicalResultBuilder"]
