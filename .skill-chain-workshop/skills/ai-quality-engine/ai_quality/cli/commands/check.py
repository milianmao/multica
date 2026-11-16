"""
backend/cli/commands/check.py

ai-quality check 命令 - 评测代码文件

对应数据流节点：B（CLI Adapter）

用法：
    ai-quality check <file> --profile code-static --format json
    ai-quality check <file> --baseline <eval_id> --min-confidence 0.5

约束（REQ-ADAPTER-01）：
- 输出合法 Canonical Result（JSON 格式）
- 非法输入有稳定非零退出码
- bin、main 与构建产物路径一致
"""

import asyncio
import json
import sys
from typing import List, Optional
from pathlib import Path

from ai_quality.engine.evaluation_core import EvaluationCore
from ai_quality.contracts.evaluation_contract import Profile


async def check_command_multi(
    paths: Optional[List[str]] = None,
    artifacts: Optional[List[dict]] = None,
    profile: str = "code-static",
    format: str = "json",
    baseline: Optional[str] = None,
    min_confidence: Optional[float] = None,
    oracle_dir: Optional[str] = None,
) -> int:
    """
    ai-quality check 命令核心（多文件 + diff 共用）

    Args:
        paths: 代码文件路径列表（普通 check；与 artifacts 二选一）
        artifacts: 预构造的 artifact 列表（diff 摄入传入；与 paths 二选一）
        profile: 评测 Profile（默认 code-static）
        format: 输出格式（json 或 text，默认 json）
        baseline: baseline evaluation ID（可选，用于改进对比）
        min_confidence: 最小置信度阈值（可选，暂未实现）
        oracle_dir: v0.7 code-verified 的 Oracle manifest 目录（可选）

    Returns:
        退出码（0=成功，非0=失败）
    """
    try:
        if artifacts is None:
            artifacts = []
            if not paths:
                raise ValueError("必须提供至少一个 path，或预构造的 artifacts")
            for file_path in paths:
                path = Path(file_path)
                if not path.exists():
                    raise FileNotFoundError(f"File not found: {file_path}")
                if not path.is_file():
                    raise NotADirectoryError(f"Not a file: {file_path}")
                content = path.read_text(encoding="utf-8")
                artifacts.append(
                    {
                        "type": "code",
                        "path": str(path),
                        "content": content,
                    }
                )

        # 执行评测（调用 EvaluationCore，单次合并所有 artifact）
        core = EvaluationCore(oracle_manifest_dir=oracle_dir)
        result = await core.evaluate(
            artifacts=artifacts,
            requested_profile=Profile(profile),
            baseline_evaluation_id=baseline,
        )

        # 输出结果
        if format == "json":
            print(result.model_dump_json(indent=2, exclude_none=False))
        else:
            print(f"Profile: {result.requested_profile.value}")
            print(f"Effective Profile: {result.effective_profile.value}")
            print(f"Status: {result.aggregate_status.value}")
            print(f"Findings: {len(result.findings)}")
            if result.findings:
                print("\nDetails:")
                for finding in result.findings:
                    source = finding.source
                    loc = f"{source.file}:{source.line_start}-{source.line_end}" if source else "?"
                    print(f"  [{finding.severity.value}] {finding.title}")
                    print(f"    Rule: {finding.rule_id}")
                    print(f"    Location: {loc}")
                    print(f"    Symptom: {finding.symptom}")
                    print(f"    Consequence: {finding.consequence}")
                    print(f"    Remedy: {finding.remedy}")
                    print()

        return 0

    except (FileNotFoundError, NotADirectoryError) as e:
        # 文件不存在/不是文件
        error_msg = f"Error: {e}"
        if format == "json":
            code = "NOT_A_FILE" if isinstance(e, NotADirectoryError) else "FILE_NOT_FOUND"
            print(json.dumps({"error": {"code": code, "message": error_msg}}))
        else:
            print(error_msg, file=sys.stderr)
        return 1

    except ValueError as e:
        # Profile 等参数错误
        error_msg = f"Error: Invalid input: {e}"
        if format == "json":
            print(json.dumps({"error": {"code": "INVALID_INPUT", "message": str(e)}}))
        else:
            print(error_msg, file=sys.stderr)
        return 2

    except Exception as e:
        # 未知错误
        error_msg = f"Error: {e}"
        if format == "json":
            print(json.dumps({"error": {"code": "INTERNAL_ERROR", "message": str(e)}}))
        else:
            print(error_msg, file=sys.stderr)
        return 1


async def check_command(
    file_path: str,
    profile: str = "code-static",
    format: str = "json",
    baseline: Optional[str] = None,
    min_confidence: Optional[float] = None,
    oracle_dir: Optional[str] = None,
) -> int:
    """
    ai-quality check 命令（单文件兼容壳）

    保持既有 python -m backend.cli.commands.check 调用形态不变，
    内部委托多文件核心 check_command_multi。
    """
    return await check_command_multi(
        paths=[file_path],
        profile=profile,
        format=format,
        baseline=baseline,
        min_confidence=min_confidence,
        oracle_dir=oracle_dir,
    )


def main():
    """CLI 入口点（用于 python -m backend.cli.commands.check）"""
    import argparse

    parser = argparse.ArgumentParser(
        description="AI Quality Check - 评测代码文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  ai-quality check test/fixtures/bad-code.ts --profile code-static --format json
  ai-quality check src/main.py --format text
  ai-quality check app.js --baseline eval_abc123 --min-confidence 0.7
        """,
    )
    parser.add_argument("file", help="代码文件路径")
    parser.add_argument(
        "--profile",
        default="code-static",
        choices=["code-static", "code-verified", "agent-verified"],
        help="评测 Profile（默认 code-static）",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="输出格式（默认 json）",
    )
    parser.add_argument(
        "--baseline",
        help="Baseline evaluation ID（用于改进对比）",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        help="最小置信度阈值（0.0-1.0）",
    )
    parser.add_argument(
        "--oracle-dir",
        help="v0.7 code-verified 的 Oracle manifest 目录（oracle-manifest.json + scenario-manifest.json）",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(
        check_command(
            file_path=args.file,
            profile=args.profile,
            format=args.format,
            baseline=args.baseline,
            min_confidence=args.min_confidence,
            oracle_dir=args.oracle_dir,
        )
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
