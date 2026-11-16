"""
backend/engine/__init__.py

Evaluation Core 模块 - 唯一规则执行器

对应数据流节点：
- I（Static Rules）
- K（Finding Ledger）
- O（Canonical Result）
"""

from ai_quality.engine.evaluation_core import EvaluationCore

__all__ = ["EvaluationCore"]
