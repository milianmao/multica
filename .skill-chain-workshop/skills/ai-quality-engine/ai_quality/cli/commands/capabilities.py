"""
backend/cli/commands/capabilities.py

ai-quality capabilities 命令 - 输出系统能力清单

对应数据流节点：B（CLI Adapter）

用法：
    ai-quality capabilities --format json
    ai-quality capabilities --format text

约束（REQ-ADAPTER-01）：
- 输出与 API Manifest 语义一致
- 非法输入有稳定非零退出码
"""

import json
import sys

from ai_quality.profile.capability_manifest import CapabilityManifest


def capabilities_command(format: str = "json") -> int:
    """
    ai-quality capabilities 命令

    Args:
        format: 输出格式（json 或 text，默认 json）

    Returns:
        退出码（0=成功，非0=失败）
    """
    try:
        # 获取默认 Capability Manifest
        manifest = CapabilityManifest.get_default_manifest()

        if format == "json":
            # 序列化为 JSON（符合契约规范 §3.3）
            print(manifest.model_dump_json(indent=2, exclude_none=False))
        else:
            # 文本摘要格式
            print(f"Schema Version: {manifest.schema_version}")
            print(f"Engine Version: {manifest.engine_version}")
            print()
            print("Profiles:")
            for name, info in manifest.profiles.items():
                status_icon = "✓" if info.status.value == "enabled" else "✗"
                print(f"  {status_icon} {name}: {info.status.value}")
                if info.reason:
                    print(f"      Reason: {info.reason}")
                if info.allowed_claims:
                    print(f"      Allowed Claims: {', '.join(info.allowed_claims)}")
                if info.forbidden_claims:
                    print(f"      Forbidden Claims: {', '.join(info.forbidden_claims)}")
                if info.required_evidence:
                    print(f"      Required Evidence: {', '.join(info.required_evidence)}")
            print()
            print("Commands:")
            for name, info in manifest.commands.items():
                status_icon = "✓" if info.status.value == "enabled" else "✗"
                print(f"  {status_icon} {name}: {info.status.value}")
                if info.reason:
                    print(f"      Reason: {info.reason}")
                if info.options:
                    print(f"      Options: {', '.join(info.options)}")

        return 0

    except Exception as e:
        # 未知错误
        error_msg = f"Error: {e}"
        if format == "json":
            print(json.dumps({"error": {"code": "INTERNAL_ERROR", "message": str(e)}}))
        else:
            print(error_msg, file=sys.stderr)
        return 1


def main():
    """CLI 入口点（用于 python -m backend.cli.commands.capabilities）"""
    import argparse

    parser = argparse.ArgumentParser(
        description="AI Quality Capabilities - 输出系统能力清单",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  ai-quality capabilities --format json
  ai-quality capabilities --format text
        """,
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="输出格式（默认 json）",
    )

    args = parser.parse_args()

    exit_code = capabilities_command(format=args.format)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
