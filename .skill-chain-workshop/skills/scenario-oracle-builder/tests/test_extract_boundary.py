"""scenario extractor 边界语义兜底与 covered 判据回归测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "extract.py"


def _load_extractor():
    spec = importlib.util.spec_from_file_location("scenario_extract_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _flow_block(lines):
    return ("测试流", lines)


def test_boundary_hints_from_error_path():
    module = _load_extractor()
    flow = module._parse_flow_block(
        _flow_block([
            "## 触发条件",
            "- 用户提交",
            "## 数据流",
            "- 创建订单",
            "## 异常处理",
            "- 库存不足时降级为占位",
            "- 并发重复提交冲突返回 409",
            "- 超时重试",
        ]),
        "order.md",
    )
    assert flow.status == module.FLOW_STATUS_COVERED
    assert any("降级" in case for case in flow.boundary_cases)
    assert any("冲突" in case for case in flow.boundary_cases)
    assert any("超时" in case for case in flow.boundary_cases)


def test_no_boundary_hints_keeps_covered():
    module = _load_extractor()
    flow = module._parse_flow_block(
        _flow_block([
            "## 触发条件",
            "- 用户提交",
            "## 数据流",
            "- 创建订单",
            "## 异常处理",
            "- 抛异常",
        ]),
        "order.md",
    )
    # covered 判据不变：无边界词不降级，boundary_cases 为空
    assert flow.status == module.FLOW_STATUS_COVERED
    assert flow.boundary_cases == []


def test_explicit_boundary_section_still_works():
    module = _load_extractor()
    flow = module._parse_flow_block(
        _flow_block([
            "## 触发条件",
            "- 用户提交",
            "## 数据流",
            "- 创建订单",
            "## 异常处理",
            "- 抛异常",
            "## 边界情况",
            "- 空列表",
            "- 超长输入",
        ]),
        "order.md",
    )
    assert flow.status == module.FLOW_STATUS_COVERED
    assert any("空列表" in case for case in flow.boundary_cases)
    assert any("超长输入" in case for case in flow.boundary_cases)
