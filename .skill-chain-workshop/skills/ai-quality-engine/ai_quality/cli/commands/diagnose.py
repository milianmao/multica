"""
backend/cli/commands/diagnose.py

ai-quality diagnose 命令 - 全项目诊断（健康分 + 维度分布 + Top-N 问题）

对应数据流节点：B（CLI Adapter）
设计文档：v0.4 设计文档 §6/§7/§8

用法：
    ai-quality diagnose <path> --format markdown
    ai-quality diagnose <path> --format json --top-n 20

数据流：
    project_scanner.discover() -> EvaluationCore.evaluate() -> aggregator.aggregate()
    -> report_renderer.render() -> stdout
"""

import asyncio
import json
import sys
from pathlib import Path

from ai_quality.cli.aggregator import aggregate
from ai_quality.cli.project_scanner import discover
from ai_quality.cli.report_renderer import render
from ai_quality.contracts.evaluation_contract import Profile
from ai_quality.engine.evaluation_core import EvaluationCore
from ai_quality.rules.suppression import apply_suppressions


async def diagnose_command(
    path: str,
    format: str = "markdown",
    top_n: int = 10,
) -> int:
    """ai-quality diagnose 命令核心。

    Args:
        path: 项目目录路径
        format: 输出格式（json 或 markdown，默认 markdown）
        top_n: Top-N 问题数量（默认 10）

    Returns:
        退出码（0=成功，非0=失败）
    """
    try:
        artifacts, warnings = discover(path)
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)

        # 复用单一 EvaluationCore 评测路径（与 check / check --diff 同一 Core）
        core = EvaluationCore()
        result = await core.evaluate(
            artifacts=artifacts,
            requested_profile=Profile("code-static"),
        )

        # 内联抑制：聚合前过滤被 # aiq-ignore 豁免的误报
        findings, suppressed = apply_suppressions(result.findings, artifacts)
        if suppressed:
            print(
                f"已抑制 {suppressed} 条 finding（# aiq-ignore）",
                file=sys.stderr,
            )

        report = aggregate(
            findings,
            project_path=str(Path(path).resolve()),
            top_n=top_n,
        )
        print(render(report, format=format))
        return 0

    except ValueError as e:
        # fail-closed：非目录 / 无代码文件等输入错误
        error_msg = f"Error: {e}"
        if format == "json":
            print(json.dumps({"error": {"code": "DIAGNOSE_UNAVAILABLE", "message": str(e)}}))
        else:
            print(error_msg, file=sys.stderr)
        return 1

    except Exception as e:
        # 未知错误
        error_msg = f"Error: {e}"
        if format == "json":
            print(json.dumps({"error": {"code": "INTERNAL_ERROR", "message": str(e)}}))
        else:
            print(error_msg, file=sys.stderr)
        return 1


def main():
    """CLI 入口点（用于 python -m ai_quality.cli.commands.diagnose）"""
    import argparse

    parser = argparse.ArgumentParser(
        description="AI Quality Check - 全项目诊断",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  ai-quality diagnose src/ --format markdown
  ai-quality diagnose . --format json --top-n 20
        """,
    )
    parser.add_argument("path", help="要诊断的项目目录路径")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="输出格式（默认 markdown）",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Top-N 问题数量（默认 10）",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(
        diagnose_command(
            path=args.path,
            format=args.format,
            top_n=args.top_n,
        )
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
