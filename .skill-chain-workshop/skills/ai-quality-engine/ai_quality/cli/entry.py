"""
backend/cli/entry.py

ai-quality - AI Coding 质量检查 CLI 统一入口

子命令路由：
    ai-quality --version
    ai-quality --help
    ai-quality capabilities [--format json|text]
    ai-quality check <path>... [--profile code-static] [--format json|text]
                              [--baseline <id>] [--min-confidence <0-1>]
    ai-quality check --diff [--profile code-static] [--format json|text]
    ai-quality diagnose <path> [--format json|markdown] [--top-n <n>]

对应数据流节点：B（CLI Adapter）
该文件是 console_scripts 入口（pyproject.toml: ai-quality = backend.cli.entry:main），
只做子命令路由，不持有评测逻辑（评测逻辑在 commands/check.py、commands/diagnose.py）。
"""

import argparse
import asyncio
import sys

from ai_quality import __version__
from ai_quality.cli.commands.capabilities import capabilities_command
from ai_quality.cli.commands.check import check_command_multi
from ai_quality.cli.commands.diagnose import diagnose_command
from ai_quality.cli.diff_source import build_from_git_diff

# 版本单一来源：与 pyproject.toml 的 [project].version 及 backend.__version__ 保持一致
VERSION = __version__


def _build_check_subparser(subparsers) -> None:
    """注册 check 子命令（含 --diff 开关与通用评测参数）。"""
    check_parser = subparsers.add_parser(
        "check",
        help="检查代码文件或当前 git 改动的质量",
        description="检查代码文件或当前 git 改动的质量",
    )
    # --diff 与 <path>... 互斥：要么扫 git 改动，要么显式指定文件。
    # 注：argparse 的 mutually_exclusive_group 要求成员均为可选参数（flag），
    # 不支持 positional nargs="*"，故改为手动在 _run_check 中做互斥校验。
    check_parser.add_argument(
        "--diff",
        action="store_true",
        help="扫描当前 git 改动（unstaged + staged 的代码文件）",
    )
    check_parser.add_argument(
        "paths",
        nargs="*",
        metavar="path",
        help="要检查的代码文件路径（一个或多个；与 --diff 互斥）",
    )
    check_parser.add_argument(
        "--profile",
        default="code-static",
        choices=["code-static", "code-verified", "agent-verified"],
        help="评测 Profile（默认 code-static）",
    )
    check_parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="输出格式（默认 json）",
    )
    check_parser.add_argument(
        "--baseline",
        help="Baseline evaluation ID（用于改进对比）",
    )
    check_parser.add_argument(
        "--min-confidence",
        type=float,
        help="最小置信度阈值（0.0-1.0）",
    )
    check_parser.add_argument(
        "--oracle-dir",
        help="v0.7 code-verified 的 Oracle manifest 目录（oracle-manifest.json + scenario-manifest.json）",
    )


def _build_diagnose_subparser(subparsers) -> None:
    """注册 diagnose 子命令（全项目诊断：健康分 + 维度分布 + Top-N 问题）。"""
    diag_parser = subparsers.add_parser(
        "diagnose",
        help="对项目目录做全量诊断（健康分 + 维度分布 + Top-N 问题）",
        description="对项目目录做全量诊断（健康分 + 维度分布 + Top-N 问题）",
    )
    diag_parser.add_argument(
        "path",
        metavar="path",
        help="要诊断的项目目录路径",
    )
    diag_parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="输出格式（默认 markdown）",
    )
    diag_parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Top-N 问题数量（默认 10）",
    )


def _run_check(args) -> int:
    """执行 check 子命令：根据 --diff 与否选择摄入路径。"""
    if args.diff and args.paths:
        # --diff 与 <path>... 手动互斥校验
        error_msg = "Error: --diff 与 <path> 不能同时使用"
        if args.format == "json":
            print('{"error": {"code": "INVALID_INPUT", "message": "%s"}}' % error_msg.replace('"', '\\"'))
        else:
            print(error_msg, file=sys.stderr)
        return 2
    if args.diff:
        try:
            artifacts = build_from_git_diff()
        except RuntimeError as e:
            # fail-closed：非 git / 无代码变更等应报明确错误并退出非零
            error_msg = f"Error: {e}"
            if args.format == "json":
                print('{"error": {"code": "DIFF_NOT_AVAILABLE", "message": "%s"}}' % error_msg.replace('"', '\\"'))
            else:
                print(error_msg, file=sys.stderr)
            return 1
        exit_code = asyncio.run(
            check_command_multi(
                artifacts=artifacts,
                profile=args.profile,
                format=args.format,
                baseline=args.baseline,
                min_confidence=args.min_confidence,
                oracle_dir=args.oracle_dir,
            )
        )
        return exit_code
    else:
        if not args.paths:
            # 既没 --diff 也没给路径：报错引导
            error_msg = "Error: check 需要至少一个 <path>，或使用 --diff 扫描 git 改动"
            if args.format == "json":
                print('{"error": {"code": "INVALID_INPUT", "message": "%s"}}' % error_msg.replace('"', '\\"'))
            else:
                print(error_msg, file=sys.stderr)
            return 2
        exit_code = asyncio.run(
            check_command_multi(
                paths=list(args.paths),
                profile=args.profile,
                format=args.format,
                baseline=args.baseline,
                min_confidence=args.min_confidence,
                oracle_dir=args.oracle_dir,
            )
        )
        return exit_code


def _run_diagnose(args) -> int:
    """执行 diagnose 子命令：全项目扫描 + 聚合 + 渲染。"""
    return asyncio.run(
        diagnose_command(
            path=args.path,
            format=args.format,
            top_n=args.top_n,
        )
    )


def main(argv=None) -> int:
    """CLI 入口函数（console_scripts 调用点）。"""
    parser = argparse.ArgumentParser(
        prog="ai-quality",
        description="AI Quality Check - 本地静态规则即时质检 + git diff 摄入 + 修复指导闭环",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  ai-quality --version
  ai-quality capabilities --format text
  ai-quality check test/fixtures/bad-code.ts --profile code-static --format json
  ai-quality check src/a.py src/b.py --format text
  ai-quality check --diff --format json
  ai-quality diagnose src/ --format markdown
        """,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", required=False)

    _build_check_subparser(subparsers)
    _build_diagnose_subparser(subparsers)

    cap_parser = subparsers.add_parser(
        "capabilities",
        help="输出系统能力清单",
        description="输出系统能力清单",
    )
    cap_parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="输出格式（默认 json）",
    )

    args = parser.parse_args(argv)

    if args.command == "capabilities":
        return capabilities_command(format=args.format)
    elif args.command == "check":
        return _run_check(args)
    elif args.command == "diagnose":
        return _run_diagnose(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
