"""
backend/cli/report_renderer.py

ai-quality diagnose 报告渲染器

对应数据流节点：B（CLI Adapter）
设计文档：v0.4 设计文档 §8

职责：
    render(report, format) -> str
    json：完整序列化；markdown：人类可读报告（纯字符串拼接，无第三方模板依赖）。
"""

import json
from typing import Dict, List


def _report_dict(report) -> Dict:
    """把 ProjectReport 序列化为可 JSON 化的 dict。"""
    return {
        "project_path": report.project_path,
        "health_score": report.health_score,
        "total_findings": report.total_findings,
        "triggered_dimensions": report.triggered_dimensions,
        "dimensions": [
            {
                "dimension": d.dimension,
                "total": d.total,
                "critical": d.critical,
                "error": d.error,
                "warning": d.warning,
                "info": d.info,
            }
            for d in report.dimensions
        ],
        "top_n": report.top_n,
        "generated_at": report.generated_at,
    }


def render(report, format: str = "markdown") -> str:
    """按 format 渲染报告为字符串。"""
    if format == "json":
        return json.dumps(_report_dict(report), ensure_ascii=False, indent=2)
    return _render_markdown(report)


def _render_markdown(report) -> str:
    """渲染 Markdown 项目诊断报告（结构见设计文档 §8.2）。"""
    lines: List[str] = []
    lines.append("# 项目质量诊断报告")
    lines.append("")
    lines.append(f"- **项目路径**: `{report.project_path}`")
    lines.append(f"- **生成时间**: {report.generated_at}")
    lines.append(f"- **健康分**: {report.health_score}/100")
    lines.append("")
    lines.append("## 概览")
    lines.append("")
    lines.append(f"- **总 findings**: {report.total_findings}")
    lines.append(f"- **触发维度**: {report.triggered_dimensions}/7")
    lines.append("")
    lines.append("## 维度分布")
    lines.append("")
    lines.append("| 维度 | 总计 | Critical | Error | Warning | Info |")
    lines.append("|------|------|----------|-------|---------|------|")
    if report.dimensions:
        for d in report.dimensions:
            lines.append(f"| {d.dimension} | {d.total} | {d.critical} | {d.error} | {d.warning} | {d.info} |")
    else:
        lines.append("| - | 0 | 0 | 0 | 0 | 0 |")
    lines.append("")
    lines.append("## Top-N 问题")
    lines.append("")
    if not report.top_n:
        lines.append("未发现质量问题。")
    else:
        for i, item in enumerate(report.top_n, start=1):
            lines.append(f"### {i}. {item['title']} (`{item['rule_id']}`)")
            lines.append("")
            location = item.get("file") or "?"
            if item.get("line_start") is not None:
                location = f"{location}:{item['line_start']}-{item.get('line_end')}"
            lines.append(f"- **严重度**: {item['severity']} | **置信度**: {item['confidence']}")
            lines.append(f"- **维度**: {item['dimension']}")
            lines.append(f"- **位置**: `{location}`")
            if item.get("symptom"):
                lines.append(f"- **症状**: {item['symptom']}")
            if item.get("consequence"):
                lines.append(f"- **后果**: {item['consequence']}")
            if item.get("remedy"):
                lines.append(f"- **修复建议**: {item['remedy']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
