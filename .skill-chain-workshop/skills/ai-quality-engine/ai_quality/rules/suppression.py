"""
ai_quality/rules/suppression.py

内联抑制（inline suppression）— aiq-ignore 指令解析与过滤

对标 aislop 的内联抑制能力（aislop-ignore-line/next-line/file），
支持三种粒度的豁免注释，让 AI 消费报告时能精准豁免误报：

    # aiq-ignore             豁免当前行（等价 # aiq-ignore-line）
    # aiq-ignore-line        豁免当前行
    # aiq-ignore-next-line   豁免下一行
    # aiq-ignore-file        豁免整个文件

可选规则过滤（更精准）：
    # aiq-ignore-line R05    仅豁免当前行的 R05 finding
    # aiq-ignore-file R04    仅豁免整个文件的 R04 finding

判定策略：tokenize 扫描注释（非纯正则），字符串字面量内的 "# aiq-ignore"
不会被误判为指令。

数据流位置：聚合前过滤——findings 经 apply_suppressions 后进入 aggregate，
保证 health_score / top_n 不把已豁免项计入扣分。
"""

from __future__ import annotations

import io
import tokenize
from typing import Any, Dict, List, Optional, Tuple

# 指令前缀与别名（不含可选 rule_id 参数）
_DIRECTIVE_PREFIXES = {
    "aiq-ignore-line": "line",
    "aiq-ignore-next-line": "next-line",
    "aiq-ignore-file": "file",
    "aiq-ignore": "line",  # 默认等价 line
}


def _parse_directives(
    content: str,
) -> List[Tuple[int, str, Optional[str]]]:
    """
    解析代码中的 aiq-ignore 指令

    Returns:
        [(line_num, scope, rule_filter), ...]
        scope: line | next-line | file
        rule_filter: 规则 ID（如 "R05"）或 None（豁免所有规则）
    """
    directives: List[Tuple[int, str, Optional[str]]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(content).readline)
        for tok in tokens:
            if tok.type != tokenize.COMMENT:
                continue
            text = tok.string.strip()
            line = tok.start[0]
            # 提取指令体：去掉 # 前缀后检查
            body = text.lstrip("#").strip()
            if not body.startswith("aiq-ignore"):
                continue
            parts = body.split()
            if not parts:
                continue
            directive = parts[0]
            if directive not in _DIRECTIVE_PREFIXES:
                continue
            scope = _DIRECTIVE_PREFIXES[directive]
            rule_filter = parts[1] if len(parts) > 1 else None
            directives.append((line, scope, rule_filter))
    except (tokenize.TokenError, IndentationError):
        pass  # 不可解析代码 → 忽略抑制指令，不阻塞
    return directives


def _build_suppression_map(
    artifacts: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    构建文件 → 豁免规则的映射

    Returns:
        {
            file_path: {
                "lines": set[int],              # 按行豁免（line + next-line）
                "rules_lines": dict[rule_id, set[int]],  # 按规则+行豁免
                "file_all": bool,               # 整文件豁免
                "file_rules": set[str],         # 整文件按规则豁免
            }
        }
    """
    result: Dict[str, Dict[str, Any]] = {}
    for artifact in artifacts:
        path = artifact.get("path", "")
        content = artifact.get("content", "")
        if not path:
            continue
        directives = _parse_directives(content)
        if not directives:
            continue

        entry = result.setdefault(
            path,
            {
                "lines": set(),
                "rules_lines": {},
                "file_all": False,
                "file_rules": set(),
            },
        )

        for line, scope, rule_filter in directives:
            if scope == "file":
                if rule_filter is None:
                    entry["file_all"] = True
                else:
                    entry["file_rules"].add(rule_filter)
            elif scope == "next-line":
                target = line + 1
                _add_line_entry(entry, target, rule_filter)
            else:  # line
                _add_line_entry(entry, line, rule_filter)
    return result


def _add_line_entry(
    entry: Dict[str, Any], target: int, rule_filter: Optional[str]
) -> None:
    """向 entry 添加一条按行豁免"""
    if rule_filter is None:
        entry["lines"].add(target)
    else:
        entry.setdefault("rules_lines", {}).setdefault(rule_filter, set()).add(target)


def apply_suppressions(
    findings: List[Any],
    artifacts: List[Dict[str, Any]],
) -> Tuple[List[Any], int]:
    """
    应用内联抑制，过滤被豁免的 findings

    Args:
        findings: CanonicalResult.findings（或 Finding 列表）
        artifacts: 规范化后的 Artifact 列表（含 path/content）

    Returns:
        (过滤后 findings, 被豁免数量)
    """
    if not findings:
        return findings, 0

    supp_map = _build_suppression_map(artifacts)
    if not supp_map:
        return findings, 0

    filtered: List[Any] = []
    suppressed = 0

    for finding in findings:
        source = getattr(finding, "source", None)
        file_path = getattr(source, "file", None)
        line = getattr(source, "line_start", None)
        rule_id = getattr(finding, "rule_id", None)

        entry = supp_map.get(file_path) if file_path else None
        if entry is None:
            filtered.append(finding)
            continue

        # 整文件豁免
        if entry["file_all"]:
            suppressed += 1
            continue
        if rule_id and rule_id in entry["file_rules"]:
            suppressed += 1
            continue

        # 按行豁免（全部规则）
        if line is not None and line in entry["lines"]:
            suppressed += 1
            continue

        # 按规则 + 行豁免
        if rule_id and line is not None:
            rules_lines = entry.get("rules_lines", {})
            if rule_id in rules_lines and line in rules_lines[rule_id]:
                suppressed += 1
                continue

        filtered.append(finding)

    return filtered, suppressed
