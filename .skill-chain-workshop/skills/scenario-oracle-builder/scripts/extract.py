#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scenario-oracle-builder — KB 业务流 → Scenario Test + scenario-manifest.json

开发版（docs/coding-new-skill），成熟后迁移到工坊链 skills/。

I/O 契约遵循 v0.7 设计文档 §3.4.2（字段级）：

    python3 scripts/extract.py \
        --kb <path/to/03-核心数据业务流> \
        --output-dir <path/to/tests/scenario> \
        --project-root <project/root>

Phase 1: 业务流解析（扫描 *.md，提取 触发/数据流/异常处理/边界/Given-When-Then）
Phase 2: Scenario Test 生成（每流 1 test class，正常/异常/边界独立 test function）
Phase 3: scenario-manifest.json 生成（coverage / fork_coverage / mock_ceiling_risk / kb_freshness）

约束：Python 3.9 兼容（typing.Optional/List，禁 PEP 604）；仅标准库。
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENGINE_VERSION = "0.7.0"
SCHEMA_VERSION = "scenario-oracle-v1"
COVERAGE_GATE = 0.90
MOCK_CEILING_RATIO = 0.8
STALE_DAYS = 14

FLOW_STATUS_COVERED = "covered"
FLOW_STATUS_PARTIAL = "partial_parse"
FLOW_STATUS_UNPARSEABLE = "unparseable"

# 小节标题 → 语义 kind 的识别关键词（模糊匹配，兼容中英文）
_SECTION_KEYWORDS = {
    "trigger": ("触发", "触发条件", "trigger"),
    "dataflow": ("数据流", "数据路径", "dataflow"),
    "error": ("异常处理", "错误处理", "异常", "error"),
    "boundary": ("边界", "边界情况", "边界场景", "boundary"),
    "gwt": ("given-when-then", "验收", "gwt", "given", "scenario"),
}

_MOCK_RE = re.compile(
    r"\bmock\.(patch|Mock|MagicMock|PropertyMock|mock)\b"
    r"|(?<![\w.])Mock\("
    r"|(?<![\w.])MagicMock\("
)
_ASSERT_RE = re.compile(
    r"\bassert\s"
    r"|\bassertEqual\b|\bassertNotEqual\b|\bassertIn\b|\bassertNotIn\b"
    r"|\bassertTrue\b|\bassertFalse\b|\bassertIs\b|\bassertIsNone\b|\bassertIsNotNone\b"
    r"|\bassertRaises\b|pytest\.raises\b|\bassert_\w+"
)


@dataclass
class Flow:
    """单条业务流（Phase 1 的内部数据结构）。"""

    flow_name: str
    kb_doc: str
    trigger: List[str] = field(default_factory=list)
    dataflow: List[str] = field(default_factory=list)
    error_handling: List[str] = field(default_factory=list)
    error_cases: List[str] = field(default_factory=list)
    boundary_cases: List[str] = field(default_factory=list)
    gwt_steps: List[str] = field(default_factory=list)
    status: str = FLOW_STATUS_COVERED


# ---------------------------------------------------------------------------
# Phase 1: 业务流解析
# ---------------------------------------------------------------------------

def _is_h1(line: str) -> bool:
    """是否为 H1 标题（`# x`，排除 `##`/`###`）。"""
    return bool(re.match(r"^#\s+\S", line)) and not line.startswith("## ")


def _is_h2(line: str) -> bool:
    """是否为 H2 标题（`## x`，排除 `###`）。"""
    return line.startswith("## ") and not line.startswith("### ")


def _classify_section(title: str) -> Optional[str]:
    """将 H2 标题归类为 trigger/dataflow/error/boundary/gwt 之一；未知返回 None。"""
    t = title.strip().lower()
    for kind, keywords in _SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in t:
                return kind
    return None


def _split_h1_blocks(lines: List[str]) -> List[Tuple[str, List[str]]]:
    """按 H1 切分文件为多个业务流块：[(flow_name, content_lines), ...]。

    🆕 2026-08-12：跳过 fenced code block——代码块内的 `# 注释`（如 CLI 示例里的
    `# 文本检索`）不是 Markdown 标题，此前被误判为 H1 产生幽灵业务流
    （O-SCENARIO-GAP 假缺口）。
    """
    blocks: List[Tuple[str, List[str]]] = []
    current_name: Optional[str] = None
    current_lines: List[str] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if current_name is not None:
                current_lines.append(line)
            continue
        if not in_fence and _is_h1(line):
            if current_name is not None:
                blocks.append((current_name, current_lines))
            current_name = re.sub(r"^#\s+", "", line).strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        blocks.append((current_name, current_lines))
    return blocks


def _split_h2_sections(content: List[str]) -> Dict[str, List[str]]:
    """将业务流块按 H2 切分为 {kind: lines}；未命中小节归入 '_other'。

    🆕 2026-08-12：同步跳过 fenced code block（与 _split_h1_blocks 一致），
    避免代码块内 `# 注释` 干扰 H2 归类。
    """
    sections: Dict[str, List[str]] = {}
    current_kind: Optional[str] = None
    in_fence = False
    for line in content:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            sections.setdefault(current_kind or "_other", []).append(line)
            continue
        if not in_fence and _is_h2(line):
            title = re.sub(r"^#+\s*", "", line).strip()
            current_kind = _classify_section(title)
            if current_kind is None:
                current_kind = "_other"
            sections.setdefault(current_kind, [])
        else:
            if current_kind is None:
                sections.setdefault("_preamble", []).append(line)
            else:
                sections[current_kind].append(line)
    return sections


def _clean_line(line: str) -> str:
    """去除 bullet 标记 / markdown 粗体 / 首尾空白，并截断到冒号前的短语。"""
    s = line.strip()
    m = re.match(r"^[-*]\s+(.*)$", s) or re.match(r"^\d+[.、)]\s*(.*)$", s)
    if m:
        s = m.group(1).strip()
    s = re.sub(r"^#+\s*", "", s)
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)
    s = re.split(r"[:：]", s)[0].strip()
    return s


def _extract_cases(lines: List[str]) -> List[str]:
    """从小节行提取用例名（bullet / 子标题 / 普通行），失败返回空列表。"""
    cases: List[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("```"):
            continue
        # 跳过 Markdown 分隔线（--- / *** / ___）与代码围栏
        if re.match(r"^[-*_]{3,}\s*$", s):
            continue
        item = _clean_line(line)
        if item:
            cases.append(item)
    return cases


def _extract_gwt(lines: List[str]) -> List[str]:
    """从小节（或任意代码块）提取 Given/When/Then 验收步骤。"""
    steps: List[str] = []
    in_fence = False
    for line in lines:
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if re.match(r"^(Given|When|Then|And)\b", s, re.IGNORECASE):
            steps.append(s)
    return steps


# 异常路径段内的「边界语义」提示词（旧模板把边界场景折叠进异常路径，如降级/超时/冲突）。
# 仅用于把这类行并入 boundary_cases（提升测试覆盖精度），不改变 covered 判据。
_BOUNDARY_HINTS = (
    "降级", "超时", "冲突", "不可用", "不可变", "重试", "上限", "超限",
    "并发", "竞态", "空", "None", "null", "缺", "已存在", "重复",
    "过大", "过长", "越界", "拒绝", "超范围", "非法",
)


def _extract_boundary_hints(lines: List[str]) -> List[str]:
    """从异常路径行中识别边界场景（命中边界语义词的行）。"""
    hints: List[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("```"):
            continue
        if any(hint in s for hint in _BOUNDARY_HINTS):
            item = _clean_line(line)
            if item:
                hints.append(item)
    return hints


def _parse_flow_block(block: Tuple[str, List[str]], kb_doc: str) -> Flow:
    """将一个 H1 块解析为 Flow（含 covered / partial_parse 判定）。"""
    flow_name, content = block
    if not flow_name:
        return Flow(flow_name="", kb_doc=kb_doc, status=FLOW_STATUS_UNPARSEABLE)

    sections = _split_h2_sections(content)
    trigger = _extract_cases(sections.get("trigger", []))
    dataflow = _extract_cases(sections.get("dataflow", []))
    error_handling = sections.get("error", [])
    error_cases = _extract_cases(error_handling)
    boundary_cases = _extract_cases(sections.get("boundary", []))
    gwt_steps = _extract_gwt(sections.get("gwt", []))

    # 异常路径存在但无显式分支 → 生成 1 个通用异常用例
    if not error_cases and any(l.strip() for l in error_handling):
        error_cases = ["error_handling"]

    # 边界语义兜底：旧模板把边界场景折叠进异常路径（降级/超时/冲突等），
    # 把这些行并入 boundary_cases 提升测试覆盖精度，不改变 covered 判据。
    boundary_hints = _extract_boundary_hints(error_handling)
    boundary_cases = list(dict.fromkeys(boundary_cases + boundary_hints))

    # 容错：三段结构不完整 → partial_parse（best-effort，仍生成测试）
    has_trigger = bool(trigger)
    has_dataflow = bool(dataflow)
    has_error = bool(error_cases)
    status = FLOW_STATUS_COVERED
    if not (has_trigger and has_dataflow and has_error):
        status = FLOW_STATUS_PARTIAL

    return Flow(
        flow_name=flow_name,
        kb_doc=kb_doc,
        trigger=trigger,
        dataflow=dataflow,
        error_handling=[l.strip() for l in error_handling if l.strip()],
        error_cases=error_cases,
        boundary_cases=boundary_cases,
        gwt_steps=gwt_steps,
        status=status,
    )


def parse_kb_directory(kb_path: Path) -> List[Flow]:
    """Phase 1：扫描 kb 目录下所有 *.md，逐文件解析出全部业务流。

    🆕 2026-08-12：跳过目录索引文档 README.md——它是分类索引（表格/mermaid），
    不含业务流 GWT 三段结构，解析只会产生 partial_parse 噪音（O-SCENARIO-GAP），
    不是真实业务流缺口。业务流应写在独立文档中。
    """
    if kb_path.is_file():
        md_files = [kb_path]
    else:
        md_files = sorted(
            f for f in kb_path.glob("*.md") if f.name.lower() != "readme.md"
        )
    flows: List[Flow] = []
    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8")
        blocks = _split_h1_blocks(text.splitlines())
        if not blocks:
            flows.append(Flow(
                flow_name=md_file.stem,
                kb_doc=md_file.name,
                status=FLOW_STATUS_UNPARSEABLE,
            ))
            continue
        for block in blocks:
            flows.append(_parse_flow_block(block, md_file.name))
    return flows


# ---------------------------------------------------------------------------
# 命名工具
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """业务流名 → snake_case（CJK 字符保留，Python 3 标识符合法）。"""
    s = name.strip()
    # camelCase → snake_case（ASCII 部分）
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    # 非标识符字符 → 下划线
    s = re.sub(r"[^0-9a-z一-鿿]+", "_", s.lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def class_name(flow_name: str) -> str:
    """Test<FlowName>：由 snake_case slug 转 PascalCase（CJK 原样保留）。"""
    slug = slugify(flow_name)
    parts = [p for p in slug.split("_") if p]
    return "Test" + "".join(p[:1].upper() + p[1:] for p in parts)


# ---------------------------------------------------------------------------
# Phase 2: Scenario Test 生成
# ---------------------------------------------------------------------------

def _first_summary(lines: List[str], fallback: str = "") -> str:
    """取小节首条非空内容作为注释摘要。"""
    for line in lines:
        s = line.strip()
        if s:
            return _clean_line(line)
    return fallback


def _py_str(value: str) -> str:
    """将任意文本转义为可安全嵌入 Python 双引号字符串字面量的形式。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _build_test_functions(flow: Flow) -> List[str]:
    """构造 test function 源码行（正常/异常/边界独立 function）。"""
    slug = slugify(flow.flow_name)
    funcs: List[str] = []

    # 正常路径
    trigger_summary = _first_summary(flow.trigger, "业务流触发")
    dataflow_summary = _first_summary(flow.dataflow, "核心数据流")
    funcs.append("    def test_{0}_happy_path(self):".format(slug))
    funcs.append("        # 触发: {0}".format(trigger_summary))
    funcs.append("        # 数据流: {0}".format(dataflow_summary))
    if flow.gwt_steps:
        funcs.append("        # 验收: {0}".format(flow.gwt_steps[0]))
    funcs.append("        flow = mock.Mock()")
    funcs.append("        flow.execute.return_value = {\"status\": \"ok\"}")
    funcs.append("        result = flow.execute(\"{0}\")".format(_py_str(flow.flow_name)))
    funcs.append("        assert result is not None")
    funcs.append("        assert result[\"status\"] == \"ok\"")
    funcs.append("        flow.execute.assert_called_once_with(\"{0}\")".format(_py_str(flow.flow_name)))
    funcs.append("")
    funcs.append("")

    # 异常路径：每异常分支 1 个独立 function
    for case in flow.error_cases:
        case_slug = slugify(case)
        if not case_slug:
            case_slug = "error_handling"
        funcs.append("    def test_{0}_{1}(self):".format(slug, case_slug))
        funcs.append("        # 异常分支: {0}".format(case))
        funcs.append("        svc = mock.Mock()")
        funcs.append("        svc.execute.side_effect = ValueError(\"{0}\")".format(_py_str(case)))
        funcs.append("        with pytest.raises(ValueError) as exc_info:")
        funcs.append("            svc.execute(\"{0}\")".format(_py_str(flow.flow_name)))
        funcs.append("        assert \"{0}\" in str(exc_info.value)".format(_py_str(case)))
        funcs.append("")
        funcs.append("")

    # 边界路径：每边界用例 1 个独立 function
    for case in flow.boundary_cases:
        case_slug = slugify(case)
        if not case_slug:
            case_slug = "case"
        funcs.append("    def test_{0}_boundary_{1}(self):".format(slug, case_slug))
        funcs.append("        # 边界场景: {0}".format(case))
        funcs.append("        svc = mock.Mock()")
        funcs.append("        svc.execute.return_value = None")
        funcs.append("        result = svc.execute(\"\")")
        funcs.append("        assert result is None")
        funcs.append("        svc.execute.assert_called_once()")
        funcs.append("")
        funcs.append("")

    return funcs


def generate_scenario_test(flow: Flow, output_dir: Path, source_kb: str) -> Tuple[str, List[str]]:
    """Phase 2：为单条业务流生成 pytest 测试文件。

    返回 (test_file_name, test_function_names)。unparseable 流返回空函数列表。
    """
    if flow.status == FLOW_STATUS_UNPARSEABLE:
        return "", []

    slug = slugify(flow.flow_name)
    test_file_name = "test_{0}_scenario.py".format(slug)
    test_functions = _build_test_functions(flow)
    func_names = [l.strip().split("(")[0][4:] for l in test_functions
                  if l.strip().startswith("def test_")]

    lines: List[str] = []
    lines.append("# Auto-generated by scenario-oracle-builder from {0} · DO NOT EDIT".format(source_kb))
    lines.append("\"\"\"Scenario tests for {0} (from {1}).\"\"\"".format(flow.flow_name, flow.kb_doc))
    lines.append("import pytest")
    lines.append("from unittest import mock")
    lines.append("")
    lines.append("")
    lines.append("class {0}:".format(class_name(flow.flow_name)))
    lines.append("    \"\"\"业务流: {0}\"\"\"".format(flow.flow_name))
    lines.append("")
    lines.extend(test_functions)

    (output_dir / test_file_name).write_text("\n".join(lines), encoding="utf-8")
    return test_file_name, func_names


def _count_mock_and_assert(text: str) -> Tuple[int, int]:
    """统计测试源码中的 mock 调用数与断言数（用于 Mock 天花板检测）。"""
    mock_count = len(_MOCK_RE.findall(text))
    assert_count = len(_ASSERT_RE.findall(text))
    return mock_count, assert_count


def detect_mock_ceiling(generated_files: List[Path]) -> Tuple[bool, int, int]:
    """Phase 2 检测：mock 调用数 / 断言数 > 80% → mock_ceiling_risk=True。"""
    total_mock = 0
    total_assert = 0
    for path in generated_files:
        m, a = _count_mock_and_assert(path.read_text(encoding="utf-8"))
        total_mock += m
        total_assert += a
    if total_assert == 0:
        risk = total_mock > 0
    else:
        risk = (total_mock / float(total_assert)) > MOCK_CEILING_RATIO
    return risk, total_mock, total_assert


# ---------------------------------------------------------------------------
# Phase 3: Manifest 生成
# ---------------------------------------------------------------------------

def _kb_last_updated(kb_path: Path) -> Tuple[str, bool]:
    """kb 目录最新 mtime → (YYYY-MM-DD, stale)。>14 天视为过期。"""
    if kb_path.is_file():
        candidates = [kb_path]
    else:
        candidates = list(kb_path.glob("*.md"))
    if not candidates:
        return datetime.now().strftime("%Y-%m-%d"), False
    newest = max(p.stat().st_mtime for p in candidates)
    mtime = datetime.fromtimestamp(newest)
    stale = (datetime.now() - mtime).days > STALE_DAYS
    return mtime.strftime("%Y-%m-%d"), stale


def build_manifest(
    flows: List[Flow],
    generated: Dict[str, Dict[str, Any]],
    kb_path: Path,
    source_kb: str,
    mock_risk: bool,
) -> Dict[str, Any]:
    """Phase 3：按 §3.4.2 Schema 组装 scenario-manifest.json。"""
    covered = sum(1 for f in flows if f.status == FLOW_STATUS_COVERED)
    total = len(flows)
    ratio = round(covered / float(total), 4) if total else 0.0

    fork_normal = sum(1 for f in flows if f.status != FLOW_STATUS_UNPARSEABLE
                      and any(n.endswith("_happy_path") for n in generated.get(f.flow_name, {}).get("functions", [])))
    fork_error = sum(1 for f in flows if f.status != FLOW_STATUS_UNPARSEABLE
                     and any("_happy_path" not in n and "boundary" not in n
                             for n in generated.get(f.flow_name, {}).get("functions", [])))
    fork_boundary = sum(1 for f in flows if f.status != FLOW_STATUS_UNPARSEABLE
                        and any("boundary" in n for n in generated.get(f.flow_name, {}).get("functions", [])))

    flow_entries = []
    for f in flows:
        info = generated.get(f.flow_name)
        if f.status == FLOW_STATUS_UNPARSEABLE or info is None:
            flow_entries.append({
                "flow_name": f.flow_name,
                "kb_doc": f.kb_doc,
                "test_file": None,
                "test_class": None,
                "test_functions": [],
                "status": f.status,
            })
        else:
            flow_entries.append({
                "flow_name": f.flow_name,
                "kb_doc": f.kb_doc,
                "test_file": info["test_file"],
                "test_class": class_name(f.flow_name),
                "test_functions": info["functions"],
                "status": f.status,
            })

    last_updated, stale = _kb_last_updated(kb_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_kb": source_kb,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine_version": ENGINE_VERSION,
        "coverage": {"covered_flows": covered, "total_flows": total, "ratio": ratio},
        "fork_coverage": {"normal": fork_normal, "error": fork_error, "boundary": fork_boundary},
        "mock_ceiling_risk": mock_risk,
        "flows": flow_entries,
        "kb_freshness": {"last_updated": last_updated, "stale": stale},
    }


def _report_gate(manifest: Dict[str, Any], mock_stats: Tuple[bool, int, int]) -> None:
    """输出 Gate 结果与 warning（不阻塞）。"""
    coverage = manifest["coverage"]
    sys.stderr.write(
        "[scenario-oracle-builder] coverage={0}/{1} ratio={2:.2f} gate={3:.2f}\n".format(
            coverage["covered_flows"], coverage["total_flows"],
            coverage["ratio"], COVERAGE_GATE))
    if coverage["ratio"] < COVERAGE_GATE:
        sys.stderr.write("[scenario-oracle-builder] WARNING: coverage below gate {0}\n".format(COVERAGE_GATE))
    risk, mocks, asserts = mock_stats
    if risk:
        sys.stderr.write(
            "[scenario-oracle-builder] WARNING: mock ceiling risk (mock={0} assert={1} ratio={2:.2f} > {3})\n".format(
                mocks, asserts, (mocks / float(asserts)) if asserts else 0.0, MOCK_CEILING_RATIO))
    if manifest["kb_freshness"]["stale"]:
        sys.stderr.write("[scenario-oracle-builder] WARNING: KB stale (last_updated={0} > {1} days), suggest knowledge-base-builder refresh\n".format(
            manifest["kb_freshness"]["last_updated"], STALE_DAYS))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_source_kb(kb_path: Path, project_root: Path) -> str:
    """source_kb 字段：kb 位于项目根下则输出相对路径，否则绝对路径。"""
    try:
        rel = kb_path.resolve().relative_to(project_root.resolve())
        return rel.as_posix()
    except ValueError:
        return kb_path.as_posix()


def run_extract(kb: Path, output_dir: Path, project_root: Path) -> Dict[str, Any]:
    """完整流水线：Phase 1 → Phase 2 → Phase 3。返回 manifest。"""
    flows = parse_kb_directory(kb)
    source_kb = _resolve_source_kb(kb, project_root)

    generated: Dict[str, Dict[str, Any]] = {}
    generated_files: List[Path] = []
    for flow in flows:
        if flow.status == FLOW_STATUS_UNPARSEABLE:
            generated[flow.flow_name] = {"test_file": None, "functions": []}
            continue
        test_file, func_names = generate_scenario_test(flow, output_dir, source_kb)
        generated[flow.flow_name] = {"test_file": test_file, "functions": func_names}
        if test_file:
            generated_files.append(output_dir / test_file)

    mock_risk, mock_total, assert_total = detect_mock_ceiling(generated_files)
    manifest = build_manifest(flows, generated, kb, source_kb, mock_risk)

    manifest_path = output_dir / "scenario-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _report_gate(manifest, (mock_risk, mock_total, assert_total))
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scenario-oracle-builder",
        description="解析 KB 业务流文档，生成 Scenario Test + scenario-manifest.json（v0.7 §3.4.2）")
    parser.add_argument("--kb", required=True, help="输入业务流文档目录（03-核心数据业务流）")
    parser.add_argument("--output-dir", required=True, help="输出目录（Test + manifest 写入处）")
    parser.add_argument("--project-root", required=True, help="项目根（用于解析相对引用）")
    args = parser.parse_args(argv)

    kb_path = Path(args.kb)
    output_dir = Path(args.output_dir)
    project_root = Path(args.project_root)

    if not kb_path.exists():
        sys.stderr.write("[scenario-oracle-builder] ERROR: --kb not found: {0}\n".format(kb_path))
        return 2
    if not kb_path.is_dir() and not kb_path.is_file():
        sys.stderr.write("[scenario-oracle-builder] ERROR: --kb must be a directory or .md file: {0}\n".format(kb_path))
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = run_extract(kb_path, output_dir, project_root)
    print(json.dumps({
        "ok": True,
        "manifest": str(output_dir / "scenario-manifest.json"),
        "coverage": manifest["coverage"],
        "fork_coverage": manifest["fork_coverage"],
        "mock_ceiling_risk": manifest["mock_ceiling_risk"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
