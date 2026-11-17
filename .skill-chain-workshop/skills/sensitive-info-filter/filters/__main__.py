#!/usr/bin/env python3
"""
sensitive_filter CLI — 命令行入口

Usage:
  # 单文件脱敏（输出到 stdout）
  python -m filters.sensitive_filter path/to/file.md

  # 批量扫描目录
  python -m filters.sensitive_filter --scan path/to/project --output path/to/clean

  # 仅检测不修改（dry-run）
  python -m filters.sensitive_filter --scan path/to/project --dry-run

  # 启用 AI 上下文判断
  python -m filters.sensitive_filter path/to/file.md --ai-audit
"""
import argparse
import json
import sys
from pathlib import Path

from .sensitive_filter import SensitiveFilter


def main():
    parser = argparse.ArgumentParser(
        description="敏感信息过滤器 - 4 道防线 (v1.1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s README.md
  %(prog)s --scan ./docs --output ./docs-clean
  %(prog)s --scan ./docs --dry-run
        """,
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="待过滤文件路径（与 --scan 互斥）",
    )
    parser.add_argument(
        "--scan",
        metavar="DIR",
        help="批量扫描目录",
    )
    parser.add_argument(
        "--output",
        metavar="DIR",
        help="输出目录（与 --scan 配合使用）",
    )
    parser.add_argument(
        "--wordlist",
        metavar="FILE",
        help="自定义敏感词库路径（默认: filters/wordlist.txt）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅检测，不写入文件",
    )
    parser.add_argument(
        "--ai-audit",
        action="store_true",
        help="启用 LLM 上下文判断（默认关闭）",
    )
    parser.add_argument(
        "--audit-dir",
        metavar="DIR",
        default="./.audit",
        help="审计日志输出目录（默认: ./.audit）",
    )

    args = parser.parse_args()

    # 默认 wordlist 路径
    default_wordlist = Path(__file__).parent / "wordlist.txt"
    wordlist_path = args.wordlist or (str(default_wordlist) if default_wordlist.exists() else None)

    f = SensitiveFilter(wordlist_path=wordlist_path)

    if args.scan:
        # 批量扫描
        scan_root = Path(args.scan)
        if not scan_root.exists():
            print(f"❌ 目录不存在: {scan_root}", file=sys.stderr)
            sys.exit(1)

        print(f"🔍 扫描目录: {scan_root}")
        print(f"📁 输出目录: {args.output or '(in-place)'}")
        print(f"🔒 模式: {'dry-run' if args.dry_run else '写入'}")
        print()

        output_root = Path(args.output) if args.output else None
        stats = f.filter_directory(
            scan_root,
            output_root=output_root,
            dry_run=args.dry_run,
        )

        # 打印统计
        print("─" * 50)
        print(f"📊 统计:")
        print(f"   扫描: {stats['scanned']}")
        print(f"   跳过: {stats['skipped']} (黑名单)")
        print(f"   净化: {stats['redacted']}")
        print(f"   拦截: {stats['blocked']} (高危)")
        print()

        # 打印命中详情
        for r in stats["results"]:
            status_icon = {
                "clean": "✅",
                "redacted": "🔒",
                "blocked": "⛔",
            }.get(r["status"], "·")
            if r["status"] != "clean":
                cats = ", ".join(r.get("categories", []))
                print(f"  {status_icon} {r['file']}: {r['hits']} 处命中 ({cats})")

        # 写审计日志
        audit_file = f.write_audit_log(stats, Path(args.audit_dir))
        print()
        print(f"📝 审计日志: {audit_file}")

        # 高危命中时退出码非零
        if stats["blocked"] > 0:
            print()
            print("⛔ 检测到高危命中，请人工审核后重试")
            sys.exit(2)

    elif args.file:
        # 单文件
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"❌ 文件不存在: {file_path}", file=sys.stderr)
            sys.exit(1)

        if f.should_skip_file(file_path):
            print(f"⏭️  跳过（黑名单）: {file_path}")
            sys.exit(0)

        if not f.should_scan_file(file_path):
            print(f"⏭️  跳过（非白名单扩展名）: {file_path}")
            sys.exit(0)

        result = f.filter_file(file_path)

        if result.has_high:
            print(f"⛔ 高危命中: {file_path}")
            for h in result.high_hits:
                print(f"   - {h.category} ({h.preview})")
            print()
            print("文件未修改。请人工审核后重试。")
            sys.exit(2)

        if result.hits:
            print(f"🔒 脱敏: {file_path} ({len(result.hits)} 处命中)")
            for h in result.hits:
                print(f"   - {h.severity}: {h.category} ({h.preview})")
            # 写入净化后内容
            if args.output:
                output_path = Path(args.output) / file_path.name
            else:
                output_path = file_path.with_suffix(file_path.suffix + ".clean")
            output_path.write_text(result.sanitized, encoding="utf-8")
            print(f"   写入: {output_path}")
        else:
            print(f"✅ 干净: {file_path}（无敏感信息）")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
