#!/bin/bash
# scan-and-filter.sh — 一键扫描脱敏脚本
#
# Usage:
#   ./scripts/scan-and-filter.sh <project_root> [output_root]
#
# Example:
#   ./scripts/scan-and-filter.sh /path/to/project /path/to/clean

set -euo pipefail

PROJECT_ROOT="${1:-.}"
OUTPUT_ROOT="${2:-$PROJECT_ROOT-clean}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "═══════════════════════════════════════════════════"
echo "  敏感信息过滤 — 一键扫描脱敏 (v1.1)"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  项目根: $PROJECT_ROOT"
echo "  输出根: $OUTPUT_ROOT"
echo ""

cd "$SCRIPT_DIR/.."

python3 -m filters.sensitive_filter \
    --scan "$PROJECT_ROOT" \
    --output "$OUTPUT_ROOT" \
    --audit-dir "$OUTPUT_ROOT/.audit"

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ 过滤完成，无高危命中"
elif [ $EXIT_CODE -eq 2 ]; then
    echo ""
    echo "⛔ 检测到高危命中，请人工审核"
fi

exit $EXIT_CODE
