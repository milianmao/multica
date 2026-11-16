#!/usr/bin/env python3
"""mutation_check.py - v1.3 Step 6 逆向验证工具

对目标源文件做"故意破坏"，验证测试能否发现。
防止"测试糊弄"+ AI 声明完成但业务不通。

用法：
    python3 skills/test-pyramid-builder/scripts/mutation_check.py \
        --target control_plane/knowledge/service.py \
        --tests control_plane/tests/test_knowledge/test_service.py \
        --mutations 5

退出码：
    0 = 所有 mutation 被测试发现（测试有效）
    1 = 有 mutation 未被测试发现（测试糊弄）
    2 = 工具自身错误
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


# 预定义 mutation 模板（保守、可逆、不破坏语法）
MUTATION_PATTERNS = [
    # (描述, 匹配正则, 替换字符串)
    ("return True -> return False", r"return True\b", "return False"),
    ("return False -> return True", r"return False\b", "return True"),
    ("== -> !=", r"==", "!="),
    ("!= -> ==", r"!=", "=="),
    ("raise -> pass (注释 raise)", r"^(\s*)raise ", r"\1pass  # MUTATED: raise removed"),
    (".append(x) -> .append(None)", r"\.append\(([^)]+)\)", ".append(None)  # MUTATED"),
    ("INSERT INTO -> INSERT INTO MUTATED", r"INSERT INTO (\w+)", r"INSERT INTO MUTATED_\1"),
]


def find_mutation_points(source: str, max_n: int) -> list[tuple[int, str, str, str]]:
    """在源文件中找可 mutation 的点。返回 [(行号, 原行, 描述, 替换后), ...]"""
    points = []
    for i, line in enumerate(source.splitlines(), 1):
        for desc, pattern, replacement in MUTATION_PATTERNS:
            if re.search(pattern, line):
                mutated = re.sub(pattern, replacement, line, count=1)
                if mutated != line:
                    points.append((i, line, desc, mutated))
                    if len(points) >= max_n:
                        return points
    return points


def run_tests(test_path: str, venv_python: str = None) -> tuple[bool, str]:
    """跑测试，返回 (是否通过, 输出尾部)"""
    py = venv_python or sys.executable
    try:
        result = subprocess.run(
            [py, "-m", "pytest", test_path, "-q", "--tb=line", "-x"],
            capture_output=True, text=True, timeout=120,
        )
        passed = result.returncode == 0
        tail = (result.stdout + result.stderr)[-500:]
        return passed, tail
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, f"ERROR: {e}"


def main():
    parser = argparse.ArgumentParser(description="v1.3 逆向验证 mutation check")
    parser.add_argument("--target", required=True, help="被测源文件路径")
    parser.add_argument("--tests", required=True, help="测试文件路径")
    parser.add_argument("--mutations", type=int, default=5, help="mutation 数量")
    parser.add_argument("--venv-python", default=None, help="虚拟环境 python 路径")
    args = parser.parse_args()

    target = Path(args.target)
    if not target.exists():
        print(f"❌ target not found: {target}", file=sys.stderr)
        return 2

    # 0. 先跑基线（未 mutation）-> 必须绿
    print(f"[baseline] running {args.tests} without mutation...")
    baseline_pass, baseline_tail = run_tests(args.tests, args.venv_python)
    if not baseline_pass:
        print(f"❌ baseline 测试本就红，无法做 mutation check")
        print(f"   tail: {baseline_tail}")
        return 2
    print(f"[baseline] ✅ green\n")

    source_orig = target.read_text(encoding="utf-8")
    backup = target.with_suffix(target.suffix + ".mutation-bak")
    backup.write_text(source_orig, encoding="utf-8")

    points = find_mutation_points(source_orig, args.mutations)
    if not points:
        print(f"⚠️  未找到可 mutation 的点（文件可能太小或无匹配模式）")
        return 0

    print(f"找到 {len(points)} 个 mutation 点\n")

    results = []
    try:
        for idx, (line_no, orig_line, desc, mutated_line) in enumerate(points, 1):
            # 应用 mutation
            lines = source_orig.splitlines(keepends=True)
            lines[line_no - 1] = mutated_line + ("\n" if not mutated_line.endswith("\n") else "")
            target.write_text("".join(lines), encoding="utf-8")

            print(f"[mutation {idx}/{len(points)}] line {line_no}: {desc}")
            print(f"  原代码: {orig_line.strip()[:80]}")
            print(f"  改坏后: {mutated_line.strip()[:80]}")

            passed, tail = run_tests(args.tests, args.venv_python)
            caught = not passed  # 测试红 = mutation 被发现 = 测试有效

            status = "✅ 红（测试发现 mutation）" if caught else "❌ 绿（测试未发现 -> 糊弄）"
            print(f"  结果: {status}\n")

            results.append({
                "line": line_no,
                "desc": desc,
                "orig": orig_line.strip()[:80],
                "mutated": mutated_line.strip()[:80],
                "caught": caught,
                "tail": tail[:200] if tail else "",
            })
    finally:
        # 恢复原文件
        target.write_text(source_orig, encoding="utf-8")
        backup.unlink(missing_ok=True)

    # 汇总
    caught_count = sum(1 for r in results if r["caught"])
    total = len(results)
    print("=" * 60)
    print(f"逆向验证汇总: {caught_count}/{total} mutation 被测试发现")
    print("=" * 60)
    print(f"{'#':>3}  {'Line':>5}  {'描述':<40}  {'结果':<6}")
    for i, r in enumerate(results, 1):
        flag = "✅" if r["caught"] else "❌"
        print(f"{i:>3}  {r['line']:>5}  {r['desc'][:40]:<40}  {flag}")

    if caught_count == total:
        print(f"\n✅ 结论：{total}/{total} mutation 被发现，测试有效，非糊弄。")
        return 0
    else:
        missed = total - caught_count
        print(f"\n❌ 结论：{missed}/{total} mutation 未被发现，测试糊弄，必须补强。")
        return 1


if __name__ == "__main__":
    sys.exit(main())