"""
backend/rules/__init__.py

静态规则模块 - code-static 静态规则实现

包含以下维度的规则引擎：
- maintainability（可维护性）: R01
- reliability（可靠性）: R02
- security（安全性）: R04-R06
- correctness（正确性）: R07-R09
- readability（可读性）: R10-R12
- performance（性能）: R13-R15
- complexity（复杂度）: R16-R18
"""

from ai_quality.rules.static_rules import StaticRulesEngine
from ai_quality.rules.security_rules import SecurityRulesEngine
from ai_quality.rules.correctness_rules import CorrectnessRulesEngine
from ai_quality.rules.readability_rules import ReadabilityRulesEngine
from ai_quality.rules.performance_rules import PerformanceRulesEngine
from ai_quality.rules.complexity_rules import ComplexityRulesEngine
from ai_quality.rules.aislop_rules import AislopRulesEngine
from ai_quality.rules.mock_rules import MockRulesEngine
from ai_quality.rules.assertion_rules import AssertionRulesEngine

__all__ = [
    "StaticRulesEngine",
    "SecurityRulesEngine",
    "CorrectnessRulesEngine",
    "ReadabilityRulesEngine",
    "PerformanceRulesEngine",
    "ComplexityRulesEngine",
    "AislopRulesEngine",
    "MockRulesEngine",
    "AssertionRulesEngine",
]
