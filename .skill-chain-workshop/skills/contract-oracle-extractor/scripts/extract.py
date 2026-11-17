#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contract-oracle-extractor — SDD 契约规范 → Contract Test + oracle-manifest.json。

本脚本是 contract-oracle-extractor Skill 的引擎（开发版，成熟后迁移到工坊链）。

- 输入：feat-design-planning Phase 2 产出的 契约规范.md（SDD）
- 输出：<output-dir>/test_<module>_contract.py + <output-dir>/oracle-manifest.json

Phase 流程：
- Phase 1 契约解析：提取 `### POST/GET/PUT/DELETE <path>` 端点 + 请求/响应体代码块
  + 错误响应表格 + SSE 事件表 + 枚举
- Phase 2 Contract Test 生成：每端点 1 个 schema test function + 每错误码 1 个参数化 case
- Phase 3 Oracle Manifest 生成：oracle-manifest.json（schema_version=contract-oracle-v1）

兼容性：
- Python 3.9（使用 typing.Optional/List/Dict，禁止 PEP 604 `X | None`）
- 依赖仅标准库；jsonschema 仅被「生成的测试」使用，缺失时自动降级为内置结构校验

设计契约（docs/v0.7/设计文档.md §3.4.1）：
- schema_version 固定 = "contract-oracle-v1"，供质检内核识别
- coverage.ratio = covered/total，Gate 阈值 0.80（低于仅标 warning，不阻塞）
- manifest JSON 非法 → fail-closed（拒绝加载，非零退出）
- Markdown 格式不规范时 best-effort 提取 + gap 标记（不崩溃）
"""

import argparse
import json
import os
import pprint
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ENGINE_VERSION = "0.7.0"
SCHEMA_VERSION = "contract-oracle-v1"
COVERAGE_GATE = 0.80

# ---------------------------------------------------------------------------
# Phase 1 解析用正则
# ---------------------------------------------------------------------------
# 端点 heading：`### POST /api/v1/evaluations`（可选 trailing 描述）
ENDPOINT_RE = re.compile(r"^\s*#{3,4}\s+(GET|POST|PUT|DELETE|PATCH)\s+(\S+)\s*(.*)$")
HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*)$")
# 章节编号：`### 1.1 创建评测任务` / `## 1. 接口契约`
NUMBERED_HEADING_RE = re.compile(r"^\s*#{2,4}\s+(\d+(?:\.\d+)*)\s*[\.\s]*(.*)$")
FENCE_RE = re.compile(r"^\s*```(.*)$")
REQUEST_MARKER_RE = re.compile(r"请求体|请求参数|请求示例|request body", re.IGNORECASE)
RESPONSE_MARKER_RE = re.compile(r"响应体|响应参数|响应示例|成功响应|response body|response", re.IGNORECASE)
ENUM_ASSIGN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\[[^\[\]]*\])")
ENUM_DECL_RE = re.compile(r"\benum\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{([^}]*)\}")
QUOTED_TOKEN_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'')


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------
@dataclass
class ErrorContract:
    status_code: int
    error_code: str
    description: str


@dataclass
class CodeBlock:
    lang: str
    marker: Optional[str]
    body: str


@dataclass
class ApiEndpoint:
    method: str
    path: str
    description: str
    sdd_section: str
    line: int
    raw_lines: List[str] = field(default_factory=list)
    request_schema: Optional[Any] = None
    response_schema: Optional[Any] = None
    error_contracts: List[ErrorContract] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 1: 契约解析
# ---------------------------------------------------------------------------
def parse_endpoints(md_text: str) -> List[ApiEndpoint]:
    """扫描 Markdown，提取所有 `### METHOD /path` 端点块。

    容错：heading 格式不规范时跳过该行；端点下缺代码块/表格时记 gap（由 manifest 反映）。
    """
    lines = md_text.splitlines()
    endpoints: List[ApiEndpoint] = []
    current: Optional[ApiEndpoint] = None
    last_numbered: Optional[str] = None

    for index, line in enumerate(lines):
        if HEADING_RE.match(line):
            m = ENDPOINT_RE.match(line)
            if m:
                if current is not None:
                    _parse_endpoint_block(current)
                    endpoints.append(current)
                method = m.group(1)
                path = m.group(2)
                desc = (m.group(3) or "").strip()
                sdd_section = last_numbered or (desc or "{0} {1}".format(method, path))
                current = ApiEndpoint(
                    method=method,
                    path=path,
                    description=desc,
                    sdd_section=sdd_section,
                    line=index + 1,
                )
                last_numbered = None
            else:
                nm = NUMBERED_HEADING_RE.match(line)
                if nm:
                    last_numbered = "§" + nm.group(1)
        else:
            if current is not None:
                current.raw_lines.append(line)

    if current is not None:
        _parse_endpoint_block(current)
        endpoints.append(current)
    return endpoints


def _parse_endpoint_block(endpoint: ApiEndpoint) -> ApiEndpoint:
    """从端点原始文本中提取请求/响应 JSON 示例 + 错误码表格。"""
    blocks: List[CodeBlock] = []
    marker: Optional[str] = None
    i = 0
    lines = endpoint.raw_lines
    while i < len(lines):
        fence = FENCE_RE.match(lines[i])
        if fence:
            lang = fence.group(1).strip()
            body_lines: List[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body_lines.append(lines[i])
                i += 1
            i += 1  # 跳过闭合 fence
            body = "\n".join(body_lines).strip()
            if body:
                blocks.append(CodeBlock(lang=lang, marker=marker, body=body))
            marker = None
        else:
            if REQUEST_MARKER_RE.search(lines[i]):
                marker = "request"
            elif RESPONSE_MARKER_RE.search(lines[i]):
                marker = "response"
            i += 1

    request_blocks = [b for b in blocks if b.marker == "request"]
    response_blocks = [b for b in blocks if b.marker == "response"]
    unmarked = [b for b in blocks if b.marker is None]
    # best-effort：无标记代码块按「先请求后响应」补位
    if not request_blocks and unmarked:
        request_blocks.append(unmarked.pop(0))
    if not response_blocks and unmarked:
        response_blocks.append(unmarked.pop(0))

    if request_blocks:
        endpoint.request_schema = _parse_json_value(request_blocks[0].body)
    if response_blocks:
        endpoint.response_schema = _parse_json_value(response_blocks[0].body)

    endpoint.error_contracts = _parse_endpoint_errors(lines)
    return endpoint


def _parse_json_value(body: str) -> Optional[Any]:
    """解析 JSON 示例；非 JSON 或非对象/数组/标量时返回 None（视为无 schema）。"""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    return data


# --- 错误码表格 ---
def _parse_endpoint_errors(lines: List[str]) -> List[ErrorContract]:
    for table in _tables_in_lines(lines):
        errors = _errors_from_table(table)
        if errors:
            return errors
    return []


def _tables_in_lines(lines: List[str]) -> List[List[List[str]]]:
    """提取所有 Markdown 表格（每表 = 行列表，每行 = 单元格列表）。"""
    tables: List[List[List[str]]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|"):
            table_lines: List[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            if len(table_lines) >= 3:
                rows = _split_table(table_lines)
                if rows:
                    tables.append(rows)
        else:
            i += 1
    return tables


def _split_table(table_lines: List[str]) -> List[List[str]]:
    def split_row(row: str) -> List[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        return [cell.strip() for cell in row.split("|")]

    header = split_row(table_lines[0])
    if len(table_lines) < 2 or not re.search(r"\|?\s*:?-{2,}", table_lines[1]):
        return []
    return [header] + [split_row(row) for row in table_lines[2:]]


def _column_index(header: List[str], candidates: List[str]) -> Optional[int]:
    lowered = [cell.lower() for cell in header]
    for index, cell in enumerate(lowered):
        if any(candidate in cell for candidate in candidates):
            return index
    return None


def _errors_from_table(rows: List[List[str]]) -> List[ErrorContract]:
    if not rows:
        return []
    header = rows[0]
    status_i = _column_index(header, ["状态码", "status", "http"])
    code_i = _column_index(header, ["错误码", "error code", "error_code", "code"])
    desc_i = _column_index(header, ["说明", "描述", "description", "desc", "message"])
    if status_i is None and code_i is None:
        return []

    errors: List[ErrorContract] = []
    max_col = max(index for index in (status_i, code_i, desc_i) if index is not None)
    for row in rows[1:]:
        if len(row) <= max_col:
            continue
        status_raw = row[status_i].strip() if status_i is not None else ""
        code_raw = row[code_i].strip() if code_i is not None else ""
        desc_raw = row[desc_i].strip() if desc_i is not None else ""
        if not status_raw and not code_raw:
            continue
        try:
            status_code = int(status_raw)
        except ValueError:
            status_code = 0  # best-effort：无法解析状态码时置 0
        errors.append(ErrorContract(
            status_code=status_code,
            error_code=code_raw or status_raw,
            description=desc_raw,
        ))
    return errors


# --- SSE 事件表 + 枚举（Phase 1 内部提取，不写入 manifest，避免超出 I/O 契约）---
def split_level2_sections(md_text: str) -> List[Tuple[str, List[str]]]:
    lines = md_text.splitlines()
    sections: List[Tuple[str, List[str]]] = []
    heading: Optional[str] = None
    body: List[str] = []
    for line in lines:
        m = re.match(r"^\s*##\s+(.+)$", line)
        if m:
            if heading is not None:
                sections.append((heading, body))
            heading = m.group(1).strip()
            body = []
        else:
            if heading is not None:
                body.append(line)
    if heading is not None:
        sections.append((heading, body))
    return sections


def extract_sse_events(md_text: str) -> List[Dict[str, str]]:
    """提取 `## 通信协议契约` 章节下的 SSE 事件表（可选，缺失不阻塞）。"""
    for heading, body in split_level2_sections(md_text):
        if ("通信协议" in heading) or ("sse" in heading.lower()):
            for table in _tables_in_lines(body):
                events = _sse_from_table(table)
                if events:
                    return events
    return []


def _sse_from_table(rows: List[List[str]]) -> List[Dict[str, str]]:
    if not rows:
        return []
    header = rows[0]
    event_i = _column_index(header, ["事件", "event"])
    payload_i = _column_index(header, ["payload", "数据", "字段"])
    desc_i = _column_index(header, ["说明", "描述", "description", "desc"])
    if event_i is None or payload_i is None:
        return []
    events: List[Dict[str, str]] = []
    max_col = max(index for index in (event_i, payload_i, desc_i) if index is not None)
    for row in rows[1:]:
        if len(row) <= max_col:
            continue
        events.append({
            "event": row[event_i].strip(),
            "payload": row[payload_i].strip(),
            "description": row[desc_i].strip() if desc_i is not None else "",
        })
    return events


def extract_enums(md_text: str) -> List[Dict[str, Any]]:
    """提取 `## 数据模型契约` 章节下的枚举清单（可选，缺失不阻塞）。"""
    enums: List[Dict[str, Any]] = []
    for heading, body in split_level2_sections(md_text):
        if ("数据模型" in heading) or ("enum" in heading.lower()) or ("枚举" in heading):
            for block in _code_blocks_in_lines(body):
                enums.extend(_enums_from_block(block.body))
    return enums


def _code_blocks_in_lines(lines: List[str]) -> List[CodeBlock]:
    blocks: List[CodeBlock] = []
    i = 0
    while i < len(lines):
        fence = FENCE_RE.match(lines[i])
        if fence:
            body_lines: List[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body_lines.append(lines[i])
                i += 1
            i += 1
            body = "\n".join(body_lines).strip()
            if body:
                blocks.append(CodeBlock(lang=fence.group(1).strip(), marker=None, body=body))
        else:
            i += 1
    return blocks


def _enums_from_block(body: str) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for m in ENUM_ASSIGN_RE.finditer(body):
        values = _quoted_values(m.group(2))
        if values:
            found.append({"name": m.group(1), "values": values})
    for m in ENUM_DECL_RE.finditer(body):
        values = _quoted_values(m.group(2))
        if not values:
            values = [tok.strip() for tok in m.group(2).split(",") if tok.strip()]
        if values:
            found.append({"name": m.group(1), "values": values})
    return found


def _quoted_values(text: str) -> List[str]:
    pairs = QUOTED_TOKEN_RE.findall(text)
    return [first if first else second for first, second in pairs]


# ---------------------------------------------------------------------------
# Schema 推断（JSON 示例 → jsonschema）
# ---------------------------------------------------------------------------
def infer_schema(example: Any) -> Dict[str, Any]:
    """从 JSON 示例推断最小可用 jsonschema（对象/数组/标量递归）。"""
    if isinstance(example, bool):
        return {"type": "boolean"}
    if isinstance(example, int):
        return {"type": "integer"}
    if isinstance(example, float):
        return {"type": "number"}
    if isinstance(example, str):
        return {"type": "string"}
    if isinstance(example, list):
        items: Dict[str, Any] = {}
        if example:
            items = infer_schema(example[0])
        return {"type": "array", "items": items}
    if isinstance(example, dict):
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for key, value in example.items():
            properties[str(key)] = infer_schema(value)
            required.append(str(key))
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": True,
        }
    return {}


# ---------------------------------------------------------------------------
# 命名辅助
# ---------------------------------------------------------------------------
def module_from_path(path: str) -> str:
    """测试模块名：取路径首个「资源段」（跳过 api/vN 前缀），如 /api/v1/evaluations → evaluations。"""
    parts = [p for p in path.split("/") if p and p != "api" and not re.match(r"^v\d+$", p)]
    if not parts:
        parts = [p for p in path.split("/") if p]
    return parts[0] if parts else "contract"


def slugify_path(path: str) -> str:
    """/api/v1/evaluations/{id} → api_v1_evaluations_id"""
    tokens: List[str] = []
    for part in path.split("/"):
        if not part:
            continue
        part = re.sub(r"[{}]", "", part)
        part = re.sub(r"[^A-Za-z0-9_]", "_", part)
        if part:
            tokens.append(part)
    return "_".join(tokens) if tokens else "root"


def _test_function_name(endpoint: ApiEndpoint) -> str:
    return "test_{0}_{1}_schema".format(endpoint.method.lower(), slugify_path(endpoint.path))


def _error_test_function_name(endpoint: ApiEndpoint) -> str:
    return "test_{0}_{1}_error_cases".format(endpoint.method.lower(), slugify_path(endpoint.path))


# ---------------------------------------------------------------------------
# Phase 2: Contract Test 生成
# ---------------------------------------------------------------------------
HELPER_SRC = '''\
def _check(value, schema, path):
    """Structural fallback validator used when jsonschema is not installed."""
    typ = schema.get("type")
    if typ is None:
        return
    if typ == "object":
        assert isinstance(value, dict), "{0}: expected object, got {1}".format(path, type(value).__name__)
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                _check(value[key], sub, "{0}.{1}".format(path, key))
    elif typ == "array":
        assert isinstance(value, list), "{0}: expected array, got {1}".format(path, type(value).__name__)
        for index, item in enumerate(value):
            _check(item, schema.get("items", {}), "{0}[{1}]".format(path, index))
    elif typ == "string":
        assert isinstance(value, str), "{0}: expected string, got {1}".format(path, type(value).__name__)
    elif typ == "integer":
        assert isinstance(value, int) and not isinstance(value, bool), "{0}: expected integer".format(path)
    elif typ == "number":
        assert isinstance(value, (int, float)) and not isinstance(value, bool), "{0}: expected number".format(path)
    elif typ == "boolean":
        assert isinstance(value, bool), "{0}: expected boolean".format(path)


def _validate(payload, schema, label):
    """Validate payload against a jsonschema; degrade to a structural walk if unavailable."""
    if _HAS_JSONSCHEMA:
        jsonschema.validate(payload, schema)
    else:  # pragma: no cover
        _check(payload, schema, label)
'''


def _endpoint_test_source(endpoint: ApiEndpoint) -> List[str]:
    src: List[str] = []
    func = _test_function_name(endpoint)
    src.append("def {0}():".format(func))
    src.append('    """Contract schema test for {0} {1}."""'.format(endpoint.method, endpoint.path))
    if endpoint.request_schema is not None:
        schema = infer_schema(endpoint.request_schema)
        src.append("    request_schema = {0}".format(pprint.pformat(schema, width=88, sort_dicts=False)))
        src.append("    request_payload = {0}".format(pprint.pformat(endpoint.request_schema, width=88, sort_dicts=False)))
        src.append('    _validate(request_payload, request_schema, "request")')
    if endpoint.response_schema is not None:
        schema = infer_schema(endpoint.response_schema)
        src.append("    response_schema = {0}".format(pprint.pformat(schema, width=88, sort_dicts=False)))
        src.append("    response_payload = {0}".format(pprint.pformat(endpoint.response_schema, width=88, sort_dicts=False)))
        src.append('    _validate(response_payload, response_schema, "response")')
    if endpoint.request_schema is None and endpoint.response_schema is None:
        src.append('    pytest.skip("gap: no_schema_defined")')
    if endpoint.error_contracts:
        src.append("")
        src.append("@pytest.mark.parametrize(")
        src.append('    "status_code,error_code,description",')
        src.append("    [")
        for ec in endpoint.error_contracts:
            src.append("        ({0}, {1}, {2}),".format(repr(ec.status_code), repr(ec.error_code), repr(ec.description)))
        src.append("    ],")
        src.append(")")
        src.append("def {0}(status_code, error_code, description):".format(_error_test_function_name(endpoint)))
        src.append('    """Contract: error response fields for {0} {1}."""'.format(endpoint.method, endpoint.path))
        src.append("    assert isinstance(status_code, int)")
        src.append("    assert isinstance(error_code, str)")
        src.append("    assert isinstance(description, str)")
    return src


def build_test_file_source(module: str, endpoints: List[ApiEndpoint], source_sdd: str) -> str:
    parts: List[str] = []
    parts.append("# Auto-generated by contract-oracle-extractor from {0} · DO NOT EDIT".format(source_sdd))
    parts.append('"""Contract tests for the {0} API.'.format(module))
    parts.append("")
    parts.append("Source SDD: {0}".format(source_sdd))
    parts.append("Generated by contract-oracle-extractor (engine v{0}); schema_version {1}.".format(
        ENGINE_VERSION, SCHEMA_VERSION))
    parts.append('"""')
    parts.append("")
    parts.append("import pytest")
    parts.append("")
    parts.append("try:")
    parts.append("    import jsonschema")
    parts.append("    _HAS_JSONSCHEMA = True")
    parts.append("except ImportError:  # pragma: no cover")
    parts.append("    _HAS_JSONSCHEMA = False")
    parts.append("")
    parts.append(HELPER_SRC)
    for endpoint in endpoints:
        parts.append("")
        parts.extend(_endpoint_test_source(endpoint))
    parts.append("")
    return "\n".join(parts)


def generate_test_files(endpoints: List[ApiEndpoint], source_sdd: str, output_dir: str) -> Dict[str, str]:
    """按模块分组生成 test_<module>_contract.py；返回 module → 文件名映射。

    自检：生成内容必须先通过 compile() 语法校验，失败则 fail-closed 不写入。
    """
    groups: Dict[str, List[ApiEndpoint]] = {}
    for ep in endpoints:
        groups.setdefault(module_from_path(ep.path), []).append(ep)
    os.makedirs(output_dir, exist_ok=True)

    written: Dict[str, str] = {}
    for module, eps in groups.items():
        filename = "test_{0}_contract.py".format(module)
        source = build_test_file_source(module, eps, source_sdd)
        try:
            compile(source, filename, "exec")
        except SyntaxError as exc:
            raise RuntimeError(
                "contract-oracle-extractor: FAIL-CLOSED 生成的测试文件语法自检失败: {0}: {1}".format(filename, exc))
        path = os.path.join(output_dir, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        written[module] = filename
    return written


# ---------------------------------------------------------------------------
# Phase 3: Oracle Manifest 生成
# ---------------------------------------------------------------------------
def build_manifest(
    endpoints: List[ApiEndpoint],
    test_files_by_module: Dict[str, str],
    source_sdd: str,
    project_root: str,
) -> Dict[str, Any]:
    """按 §3.4.1 字段级 Schema 组装 oracle-manifest。

    coverage 语义（本实现的设计决策）：
    - 端点 status = covered 当且仅当 请求 schema 与 响应 schema 均存在
    - 仅缺请求/响应 → gap（no_request_schema_defined / no_response_schema_defined / no_schema_defined）
    - 已 covered 但缺错误码表格 → 非阻塞记入 gaps[]（no_error_contract），不降低 coverage
    """
    contracts: List[Dict[str, Any]] = []
    gaps: List[Dict[str, str]] = []
    covered = 0
    total = len(endpoints)

    for ep in endpoints:
        module = module_from_path(ep.path)
        test_file = test_files_by_module.get(module)

        status = "covered"
        gap_reason: Optional[str] = None
        if ep.request_schema is None and ep.response_schema is None:
            status = "gap"
            gap_reason = "no_schema_defined"
        elif ep.request_schema is None:
            status = "gap"
            gap_reason = "no_request_schema_defined"
        elif ep.response_schema is None:
            status = "gap"
            gap_reason = "no_response_schema_defined"

        endpoint_label = "{0} {1}".format(ep.method, ep.path)
        if status == "covered":
            covered += 1
            if not ep.error_contracts:
                gaps.append({"endpoint": endpoint_label, "reason": "no_error_contract"})
        else:
            gaps.append({"endpoint": endpoint_label, "reason": gap_reason})

        contracts.append({
            "endpoint": endpoint_label,
            "method": ep.method,
            "path": ep.path,
            "sdd_section": ep.sdd_section,
            "test_file": test_file,
            "test_function": _test_function_name(ep),
            "status": status,
            "gap_reason": gap_reason,
        })

    ratio = round(covered / total, 2) if total else 0.0
    return {
        "schema_version": SCHEMA_VERSION,
        "source_sdd": _relativize(source_sdd, project_root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": ENGINE_VERSION,
        "coverage": {"covered": covered, "total": total, "ratio": ratio},
        "contracts": contracts,
        "gaps": gaps,
    }


def _relativize(path: str, project_root: str) -> str:
    try:
        return os.path.relpath(path, project_root)
    except ValueError:
        return path


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="contract-oracle-extractor",
        description="SDD 契约规范 → Contract Test + oracle-manifest.json（v0.7 设计文档 §3.4.1）。",
    )
    parser.add_argument("--sdd", required=True, help="输入契约规范.md（feat-design-planning Phase 2 产物）")
    parser.add_argument("--output-dir", required=True, help="输出目录（Test + oracle-manifest.json 写入处）")
    parser.add_argument("--project-root", required=True, help="项目根（用于解析相对引用 + 识别测试模块）")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.sdd):
        sys.stderr.write("[contract-oracle-extractor] FAIL-CLOSED: SDD 文件不存在: {0}\n".format(args.sdd))
        return 2

    with open(args.sdd, "r", encoding="utf-8") as fh:
        md_text = fh.read()

    # Phase 1: 契约解析
    endpoints = parse_endpoints(md_text)
    sse_events = extract_sse_events(md_text)
    enums = extract_enums(md_text)

    # Phase 2: Contract Test 生成
    test_files = generate_test_files(endpoints, args.sdd, args.output_dir)

    # Phase 3: Oracle Manifest 生成（JSON 非法 → fail-closed）
    manifest = build_manifest(endpoints, test_files, args.sdd, args.project_root)
    try:
        manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
        json.loads(manifest_json)  # round-trip 自检
    except (TypeError, ValueError) as exc:
        sys.stderr.write("[contract-oracle-extractor] FAIL-CLOSED: manifest 非法 JSON: {0}\n".format(exc))
        return 2
    manifest_path = os.path.join(args.output_dir, "oracle-manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        fh.write(manifest_json)
        fh.write("\n")

    cov = manifest["coverage"]
    sys.stderr.write(
        "[contract-oracle-extractor] endpoints={0} covered={1} ratio={2} test_files={3} "
        "sse_events={4} enums={5}\n".format(
            cov["total"], cov["covered"], cov["ratio"], len(test_files), len(sse_events), len(enums)))
    if cov["total"] == 0:
        sys.stderr.write("[contract-oracle-extractor] warning: SDD 中未发现任何端点（非阻塞）\n")
    elif cov["ratio"] < COVERAGE_GATE:
        sys.stderr.write(
            "[contract-oracle-extractor] warning: coverage {0} < gate {1}（仅警告，不阻塞）\n".format(
                cov["ratio"], COVERAGE_GATE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
