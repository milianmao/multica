#!/usr/bin/env python3
"""e2e_doctor.py - E2E 配置 + 验证能力 10 项 Gate 门禁检查

门禁模式（v2.0）：只输出 PASS / FAIL / WARN，不打分。
门禁是死的，不是分数。

用法：
    python3 e2e_doctor.py playwright.config.js
    python3 e2e_doctor.py /path/to/playwright.config.js --json

退出码：FAIL > 0 -> 退出 1（适合 CI 串联）
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


def read_config(path: Path) -> str:
    """读取配置文件内容"""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"[error] 配置文件不存在: {path}", file=sys.stderr)
        sys.exit(1)


# ==================== 配置层 Gate（Gate 1-5, 9, 10）====================

def check_webserver(content: str) -> Tuple[str, str]:
    """Gate 1: webServer 自动启停"""
    if "webServer" not in content:
        return "FAIL", "❌ webServer 字段缺失"
    if "reuseExistingServer" not in content:
        return "WARN", "⚠️  webServer.reuseExistingServer 未配置"
    return "PASS", "✅ webServer 配置存在 + reuseExistingServer 已配置"


def check_trace(content: str) -> Tuple[str, str]:
    """Gate 2: trace 保留失败现场"""
    match = re.search(r"trace\s*:\s*['\"]([^'\"]+)['\"]", content)
    if not match:
        return "FAIL", "❌ trace 配置缺失"
    mode = match.group(1)
    if mode == "on-first-retry":
        return "PASS", f"✅ trace='{mode}'"
    if mode == "on":
        return "PASS", f"✅ trace='{mode}'"
    if mode == "retain-on-failure":
        return "WARN", f"⚠️  trace='{mode}'（建议 'on-first-retry'）"
    if mode in ("off",):
        return "FAIL", f"❌ trace='{mode}'（关闭失败现场保留）"
    return "PASS", f"✅ trace='{mode}'"


def check_video(content: str) -> Tuple[str, str]:
    """Gate 3: video 保留时序证据"""
    if "video" not in content:
        return "FAIL", "❌ video 配置缺失"
    match = re.search(r"video\s*:\s*['\"]([^'\"]+)['\"]", content)
    if match:
        mode = match.group(1)
        if mode == "retain-on-failure":
            return "PASS", f"✅ video='{mode}'"
        if mode == "off":
            return "WARN", f"⚠️  video='off'（建议 'retain-on-failure'）"
        return "PASS", f"✅ video='{mode}'"
    return "WARN", "⚠️  video 字段存在但模式未识别"


def check_ci_adaptive(content: str) -> Tuple[str, str]:
    """Gate 4: reuseExistingServer 适配 CI/本地"""
    match = re.search(r"reuseExistingServer\s*:\s*([^,\n}]+)", content)
    if not match:
        return "WARN", "⚠️  reuseExistingServer 未配置"
    value = match.group(1).strip()
    if "!isCI" in value or "!process.env.CI" in value:
        return "PASS", f"✅ reuseExistingServer={value}（CI/本地自适应）"
    if value == "false":
        return "WARN", f"⚠️  reuseExistingServer={value}（本地体验差）"
    if value == "true":
        return "WARN", f"⚠️  reuseExistingServer={value}（CI 隔离性差）"
    return "PASS", f"✅ reuseExistingServer={value}"


def check_retries_config(content: str) -> Tuple[str, str]:
    """Gate 5: retries 配置（保证 trace 触发）"""
    match = re.search(r"retries\s*:\s*(\d+)", content)
    if match:
        value = int(match.group(1))
        if value >= 2:
            return "PASS", f"✅ retries={value}（CI 容忍 flaky + 触发 trace）"
        elif value == 1:
            return "PASS", f"✅ retries=1（基础配置）"
        return "WARN", f"⚠️  retries={value}（trace=on-first-retry 需要 ≥1）"
    # 检查是否用 process.env.CI 条件
    if re.search(r"retries\s*:\s*process\.env\.CI", content):
        return "PASS", "✅ retries 适配 CI（process.env.CI）"
    return "WARN", "⚠️  retries 未配置（trace 不生成）"


def check_timeout(content: str) -> Tuple[str, str]:
    """Gate 9: expect.timeout ≥ 8000ms"""
    match = re.search(r"expect\s*:\s*\{[^}]*timeout\s*:\s*(\d[\d_]*)", content)
    if match:
        value = int(match.group(1).replace("_", ""))
        if value >= 8000:
            return "PASS", f"✅ expect.timeout={value}ms"
        return "WARN", f"⚠️  expect.timeout={value}ms 偏短（建议 ≥8000ms）"
    return "WARN", "⚠️  expect.timeout 未显式配置"


def check_skip_autostart(content: str) -> Tuple[str, str]:
    """Gate 10: SKIP_AUTOSTART 兼容开关"""
    if "V5_E2E_SKIP_AUTOSTART" in content or "E2E_SKIP_AUTOSTART" in content:
        return "PASS", "✅ SKIP_AUTOSTART 开关存在"
    return "WARN", "⚠️  SKIP_AUTOSTART 开关缺失（向后兼容建议）"


def check_behavior_gate(test_dir: Path) -> Tuple[str, str]:
    """Gate 11 (v2.3): 行为门禁 - 点击后必须看到 DOM 状态变化（防 AI 声明完成但 UI 没改）"""
    if not test_dir.exists():
        return "WARN", "⚠️  测试目录不存在"

    spec_files = list(test_dir.rglob("*.spec.js")) + list(test_dir.rglob("*.spec.ts"))
    if not spec_files:
        return "WARN", "⚠️  无 spec 文件"

    # 行为门禁信号：getByRole('dialog') / toHaveCount(0)（旧 UI 删除）/ getByTestId
    behavior_signals = 0
    total_specs = len(spec_files)
    for f in spec_files:
        try:
            content = f.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        # 点击后状态变化（dialog/toHaveCount/getByTestId 是行为门禁特征）
        if "getByRole('dialog')" in content or 'getByRole("dialog")' in content:
            behavior_signals += 1
        if "toHaveCount(0)" in content:  # 旧 UI 删除断言
            behavior_signals += 1
        if "getByTestId" in content:  # 稳定行为锚点
            behavior_signals += 1

    if behavior_signals >= total_specs:
        return "PASS", f"✅ 行为门禁充分（{behavior_signals} 信号 / {total_specs} spec）"
    if behavior_signals > 0:
        return "WARN", f"⚠️  行为门禁偏弱（{behavior_signals} 信号 / {total_specs} spec，建议每 spec ≥1 个 toHaveCount/getByTestId）"
    return "FAIL", f"❌ 无行为门禁（0 信号 / {total_specs} spec，AI 声明完成无法交叉验证）"


def check_design_contract(test_dir: Path, docs_dir: Path = None) -> Tuple[str, str]:
    """Gate 12 (v2.3): 设计稿契约对比 - E2E 必须引用设计文档契约节点"""
    if not test_dir.exists():
        return "WARN", "⚠️  测试目录不存在"

    spec_files = list(test_dir.rglob("*.spec.js")) + list(test_dir.rglob("*.spec.ts"))
    if not spec_files:
        return "WARN", "⚠️  无 spec 文件"

    # 设计稿契约信号：DESIGN_CONTRACT 常量 / §x.y 引用 / docs/ 路径引用
    contract_signals = 0
    for f in spec_files:
        try:
            content = f.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        if "DESIGN_CONTRACT" in content or "§" in content or "docs/v" in content:
            contract_signals += 1

    if contract_signals >= 1:
        return "PASS", f"✅ 设计稿契约对比存在（{contract_signals} spec 引用设计文档）"
    return "WARN", "⚠️  无设计稿契约对比（建议 deep-planning 声明完成的 UI 变更都加 §x.y 契约 E2E）"


# ==================== 验证能力层 Gate（Gate 6, 7, 8）====================

def check_validation_strategy(test_dir: Path) -> Tuple[str, str]:
    """Gate 6: 验证策略分层"""
    if not test_dir.exists():
        return "WARN", "⚠️  测试目录不存在"

    has_smoke_dir = (test_dir / "smoke").exists()
    has_regression_dir = (test_dir / "regression").exists()
    has_contract_dir = (test_dir / "contract").exists()

    spec_files = list(test_dir.rglob("*.spec.js")) + list(test_dir.rglob("*.spec.ts"))
    smoke_files = sum(1 for f in spec_files if ".smoke." in f.name)
    regression_files = sum(1 for f in spec_files if ".regression." in f.name)
    contract_files = sum(1 for f in spec_files if ".contract." in f.name)

    has_layering = (has_smoke_dir or smoke_files > 0) and \
                   (has_regression_dir or regression_files > 0) and \
                   (has_contract_dir or contract_files > 0)

    if has_layering:
        return "PASS", f"✅ 分层：smoke={smoke_files} + regression={regression_files} + contract={contract_files} 条"

    if not spec_files:
        return "WARN", "⚠️  无测试文件，建议按 smoke/regression/contract 分层"

    return "WARN", "⚠️  验证策略未分层（建议 smoke/regression/contract）"


def check_selector_resilience(test_dir: Path) -> Tuple[str, str]:
    """Gate 7: selector 鲁棒性（语义查询占比 ≥ 50%）"""
    if not test_dir.exists():
        return "WARN", "⚠️  测试目录不存在"

    spec_files = list(test_dir.rglob("*.spec.js")) + list(test_dir.rglob("*.spec.ts"))
    if not spec_files:
        return "WARN", "⚠️  无 .spec 文件"

    semantic_count = 0
    hardcoded_count = 0

    for spec in spec_files:
        try:
            content = spec.read_text(encoding="utf-8")
            semantic_count += len(re.findall(r"getByRole\(|getByLabel\(|getByText\(|getByPlaceholder\(", content))
            hardcoded_count += len(re.findall(r"page\.locator\(['\"]\.?[#\.][a-zA-Z]", content))
            hardcoded_count += len(re.findall(r"page\.click\(['\"]\.?[#\.][a-zA-Z]", content))
        except Exception:
            continue

    total = semantic_count + hardcoded_count
    if total == 0:
        return "WARN", "⚠️  无 selector 可分析"

    semantic_ratio = semantic_count / total
    if semantic_ratio >= 0.5:
        return "PASS", f"✅ selector 鲁棒：语义 {semantic_count} / 硬编码 {hardcoded_count}（{semantic_ratio*100:.0f}% 语义）"
    elif semantic_ratio >= 0.2:
        return "WARN", f"⚠️  selector 偏硬编码：语义 {semantic_count} / 硬编码 {hardcoded_count}（{semantic_ratio*100:.0f}%，建议 ≥50%）"
    return "FAIL", f"❌ selector 大量硬编码：语义 {semantic_count} / 硬编码 {hardcoded_count}（UI 改版易崩）"


def check_assertion_quality(test_dir: Path) -> Tuple[str, str]:
    """Gate 8: 断言质量（弱断言占比 < 10%）"""
    if not test_dir.exists():
        return "WARN", "⚠️  测试目录不存在"

    spec_files = list(test_dir.rglob("*.spec.js")) + list(test_dir.rglob("*.spec.ts"))
    if not spec_files:
        return "WARN", "⚠️  无 .spec 文件"

    weak_patterns = [
        (r"\.toBeTruthy\(\)", "toBeTruthy"),
        (r"\.toBeDefined\(\)", "toBeDefined"),
        (r"\.toBeGreaterThan\(0\)", "toBeGreaterThan(0)"),
        (r"\.toBeGreaterThanOrEqual\(0\)", "toBeGreaterThanOrEqual(0)"),
        (r"waitForTimeout\(\d+\)", "waitForTimeout"),
    ]

    total_weak = 0
    total_strong = 0
    weak_details = []

    for spec in spec_files:
        try:
            content = spec.read_text(encoding="utf-8")
            for pattern, label in weak_patterns:
                matches = re.findall(pattern, content)
                if matches:
                    total_weak += len(matches)
                    weak_details.append(f"{spec.name}: {label}×{len(matches)}")
            strong_count = len(re.findall(r"\.toBe\([^t]", content)) + \
                          len(re.findall(r"\.toEqual\(", content)) + \
                          len(re.findall(r"\.toMatch\(", content)) + \
                          len(re.findall(r"\.toHaveProperty\(", content)) + \
                          len(re.findall(r"\.toHaveLength\(", content))
            total_strong += strong_count
        except Exception:
            continue

    if total_weak == 0 and total_strong == 0:
        return "WARN", "⚠️  无断言可分析"

    weak_ratio = total_weak / max(total_weak + total_strong, 1)
    if weak_ratio < 0.1:
        return "PASS", f"✅ 断言质量良好：弱 {total_weak} / 强 {total_strong}（{weak_ratio*100:.1f}% 弱）"
    elif weak_ratio < 0.3:
        return "WARN", f"⚠️  断言质量一般：弱 {total_weak} / 强 {total_strong}（{weak_ratio*100:.1f}% 弱，建议 <10%）。{'; '.join(weak_details[:3])}"
    return "FAIL", f"❌ 断言质量差：弱 {total_weak} / 强 {total_strong}（{weak_ratio*100:.1f}% 弱）。{'; '.join(weak_details[:3])}"


# ==================== 主流程 ====================

def parse_waiver(waiver_str: str) -> Dict[int, str]:
    """解析 --waiver 参数为 {gate_number: reason} 字典。

    格式: "Gate 8: 历史文件弱断言; Gate 5: 本地不需 retries"
    返回: {8: "历史文件弱断言", 5: "本地不需 retries"}

    约束: 每项必须附理由（防滥用），无理由的 waiver 被拒绝。
    """
    if not waiver_str:
        return {}
    waivers = {}
    for item in waiver_str.split(";"):
        item = item.strip()
        if not item:
            continue
        # 匹配 "Gate N: 理由" 或 "N: 理由"
        match = re.match(r"^(?:Gate\s*)?(\d+)\s*[:：]\s*(.+)$", item)
        if not match:
            print(f"[warn] waiver 项格式错误（应为 'Gate N: 理由'），已忽略: {item}", file=sys.stderr)
            continue
        gate_num = int(match.group(1))
        reason = match.group(2).strip()
        if not reason:
            print(f"[warn] waiver 项 Gate {gate_num} 缺理由，已忽略", file=sys.stderr)
            continue
        waivers[gate_num] = reason
    return waivers


def run_gates(config_path: Path, waivers: Dict[int, str] = None, allow_waiver: bool = True) -> Dict:
    """运行 10 项 Gate，返回门禁报告（不打分）。

    v2.2 新增 waiver 机制：
    - waivers: {gate_num: reason}，命中的 FAIL/WARN 改标 WAIVED，不阻塞
    - allow_waiver: False 时禁用 waiver（CI 模式，必须真修）
    """
    if waivers is None:
        waivers = {}
    # CI 模式禁用 waiver（强制真修）
    if not allow_waiver and waivers:
        print("[info] CI 模式禁用 waiver，所有 Gate 必须真修", file=sys.stderr)
        waivers = {}

    content = read_config(config_path)

    # 测试目录推断
    test_dir = config_path.parent / "tests" / "e2e"
    if not test_dir.exists():
        for candidate in [config_path.parent / "tests" / "e2e",
                          config_path.parent / "e2e",
                          config_path.parent / "tests"]:
            if candidate.exists():
                test_dir = candidate
                break

    gates = [
        ("Gate 1:  webServer 自动启停", check_webserver(content)),
        ("Gate 2:  trace 保留失败现场", check_trace(content)),
        ("Gate 3:  video 保留时序证据", check_video(content)),
        ("Gate 4:  reuseExistingServer 适配 CI/本地", check_ci_adaptive(content)),
        ("Gate 5:  retries 配置", check_retries_config(content)),
        ("Gate 6:  验证策略分层", check_validation_strategy(test_dir)),
        ("Gate 7:  selector 鲁棒性", check_selector_resilience(test_dir)),
        ("Gate 8:  断言质量", check_assertion_quality(test_dir)),
        ("Gate 9:  expect.timeout ≥ 8000ms", check_timeout(content)),
        ("Gate 10: SKIP_AUTOSTART 兼容开关", check_skip_autostart(content)),
        ("Gate 11: 行为门禁（v2.3）", check_behavior_gate(test_dir)),
        ("Gate 12: 设计稿契约对比（v2.3）", check_design_contract(test_dir)),
    ]

    results = []
    pass_count = warn_count = fail_count = waived_count = 0

    for name, (status, message) in gates:
        # 提取 Gate 编号
        gate_num_match = re.match(r"Gate\s*(\d+)", name)
        gate_num = int(gate_num_match.group(1)) if gate_num_match else 0

        # v2.2 waiver 机制：FAIL/WARN 命中 waiver -> WAIVED
        if status in ("FAIL", "WARN") and gate_num in waivers:
            waiver_reason = waivers[gate_num]
            results.append({
                "gate": name,
                "status": "WAIVED",
                "message": f"🔶 WAIVED (原 {status}): {message} | waiver 理由: {waiver_reason}",
                "original_status": status,
                "waiver_reason": waiver_reason,
            })
            waived_count += 1
            continue

        if status == "PASS":
            pass_count += 1
        elif status == "WARN":
            warn_count += 1
        else:
            fail_count += 1
        results.append({"gate": name, "status": status, "message": message})

    # 门禁判定：FAIL > 0 -> 阻塞；WARN > 0 -> 建议（不阻塞）；WAIVED 不计入阻塞
    if fail_count > 0:
        verdict = "BLOCKED"
        verdict_msg = f"❌ {fail_count} 项 Gate FAIL，必须修复才能跑测试"
    elif warn_count > 0:
        verdict = "PASS_WITH_WARN"
        verdict_msg = f"⚠️  {warn_count} 项 WARN（建议优化，不阻塞）"
        if waived_count > 0:
            verdict_msg += f"；{waived_count} 项 WAIVED（已豁免）"
    else:
        verdict = "PASS"
        if waived_count > 0:
            verdict_msg = f"✅ 10 项 Gate 全部通过（含 {waived_count} 项 WAIVED 豁免）"
        else:
            verdict_msg = "✅ 10 项 Gate 全部通过"

    return {
        "config_path": str(config_path),
        "verdict": verdict,
        "verdict_msg": verdict_msg,
        "stats": {
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "waived": waived_count,
            "total": len(gates),
        },
        "gates": results,
    }


def format_text_report(report: Dict) -> str:
    """格式化文本报告"""
    stats = report["stats"]
    waived_part = f" | 🔶 {stats.get('waived', 0)} WAIVED" if stats.get("waived", 0) > 0 else ""
    lines = [
        "",
        "=" * 70,
        f"E2E Gate 门禁报告 - {report['config_path']}",
        "=" * 70,
        f"判定: {report['verdict']} - {report['verdict_msg']}",
        f"统计: ✅ {stats['pass']} PASS | ⚠️  {stats['warn']} WARN | ❌ {stats['fail']} FAIL{waived_part} | 共 {stats['total']} 项",
        "",
        "-" * 70,
        "Gate 详情:",
        "-" * 70,
    ]

    for gate in report["gates"]:
        icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌", "WAIVED": "🔶"}[gate["status"]]
        lines.append(f"\n[{gate['status']}] {gate['gate']}")
        lines.append(f"  {icon} {gate['message']}")

    lines.extend([
        "",
        "=" * 70,
        "门禁模式（v2.2）:",
        "- PASS: 全通过 -> 可跑测试",
        "- WARN: 有警告 -> 建议优化，不阻塞",
        "- FAIL: 有失败 -> 必须修复才能跑测试",
        "- WAIVED: 已豁免（上游声明 pre-existing，CI 模式禁用）",
        "",
        "文档: skills/e2e-excellence/SKILL.md",
        "=" * 70,
    ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="E2E Gate 门禁检查工具（v2.2 支持 waiver）")
    parser.add_argument("config", help="Playwright 配置文件路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--waiver", default="", help="豁免 Gate（格式: 'Gate N: 理由; Gate M: 理由'，CI 模式禁用）")
    args = parser.parse_args()

    config_path = Path(args.config)
    # CI 模式禁用 waiver（强制真修）
    is_ci = bool(os.environ.get("CI"))
    waivers = parse_waiver(args.waiver) if not is_ci else {}
    report = run_gates(config_path, waivers=waivers, allow_waiver=not is_ci)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_text_report(report))

    # 退出码：FAIL > 0 -> 退出 1（CI 串联用）
    if report["stats"]["fail"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()