"""
backend/rules/complexity_rules.py

复杂度规则引擎 - 实现 complexity 维度的静态检查规则

对应数据流节点：I（Static Rules - Complexity）

实现的规则：
- R16: 函数过长检测
- R17: 嵌套过深检测
- R18: 参数过多检测

判定策略（多语言门控）：
- Python：AST 解析，基于真实语法结构（函数体行数、控制流嵌套深度、参数表）
- JS/TS：保留花括号配对启发式（无第三方 AST 依赖）
"""

from __future__ import annotations

import ast
import re
import uuid
from typing import Any, Dict, List, Tuple

from ai_quality.contracts.evaluation_contract import (
    Confidence,
    ConfidenceKind,
    Finding,
    FindingSource,
    Severity,
)
from ai_quality.rules.common import match_evidence_id


class ComplexityRulesEngine:
    """
    复杂度规则引擎

    执行 complexity 维度的静态检查规则，控制代码复杂度。
    """

    # 配置阈值
    MAX_FUNCTION_LENGTH = 30  # 函数最大行数
    MAX_NESTING_DEPTH = 4  # 最大嵌套层级
    MAX_PARAMETERS = 5  # 函数最大参数数量

    # Python 控制流节点（用于嵌套深度计算）
    # 注意：ast.Match 需 Python 3.10+，当前运行时 3.9，故不纳入
    _CONTROL_TYPES = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.With,
        ast.AsyncWith,
    )

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行所有复杂度规则

        Args:
            normalized_artifacts: 规范化后的 Artifact 列表
            evidence_manifest: Evidence Manifest

        Returns:
            Finding 列表
        """
        findings: List[Finding] = []

        for artifact in normalized_artifacts:
            evidence_id = self._find_evidence_id(artifact, evidence_manifest)

            # 规则 16: 函数过长检测
            findings.extend(self._check_function_length(artifact, evidence_id))

            # 规则 17: 嵌套过深检测
            findings.extend(self._check_nesting_depth(artifact, evidence_id))

            # 规则 18: 参数过多检测
            findings.extend(self._check_parameter_count(artifact, evidence_id))

        return findings

    def _find_evidence_id(
        self, artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
    ) -> str:
        """根据 artifact path 精确匹配对应的 evidence_id"""
        return match_evidence_id(artifact, evidence_manifest)

    # ═══════════════════════════════════════════════════════════════
    # R16 函数过长
    # ═══════════════════════════════════════════════════════════════

    def _check_function_length(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测函数过长（R16）

        Python 用 AST 计算真实函数体行数；JS/TS 保留花括号配对启发式。
        """
        if artifact.get("path", "").endswith(".py"):
            return self._check_function_length_python(artifact, evidence_id)
        return self._check_function_length_brace(artifact, evidence_id)

    def _check_function_length_python(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """Python 函数过长：AST FunctionDef/AsyncFunctionDef 的真实行数。"""
        findings: List[Finding] = []
        try:
            tree = ast.parse(artifact["content"])
        except SyntaxError:
            return findings

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            length = node.end_lineno - node.lineno + 1
            if length > self.MAX_FUNCTION_LENGTH:
                findings.append(
                    self._build_length_finding(
                        node.name, length, node.lineno, node.end_lineno,
                        artifact["path"], evidence_id,
                    )
                )
        return findings

    def _check_function_length_brace(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """JS/TS 函数过长：花括号配对（保留启发式）。"""
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        in_function = False
        function_start_line = 0
        function_name = ""
        brace_depth = 0

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()

            # 检测函数定义
            func_match = re.search(
                r'(?:function|def)\s+(\w+)\s*\(|(\w+)\s*=\s*(?:function|\()',
                stripped,
            )
            if func_match:
                in_function = True
                function_start_line = line_num
                function_name = func_match.group(1) or func_match.group(2) or "anonymous"
                brace_depth = stripped.count("{") - stripped.count("}")
                continue

            if in_function:
                brace_depth += stripped.count("{") - stripped.count("}")

                # 函数结束
                if brace_depth <= 0:
                    function_length = line_num - function_start_line + 1
                    if function_length > self.MAX_FUNCTION_LENGTH:
                        findings.append(
                            self._build_length_finding(
                                function_name, function_length,
                                function_start_line, line_num,
                                artifact["path"], evidence_id,
                            )
                        )
                    in_function = False

        return findings

    # ═══════════════════════════════════════════════════════════════
    # R17 嵌套过深
    # ═══════════════════════════════════════════════════════════════

    def _check_nesting_depth(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测嵌套过深（R17）

        Python 用 AST 计算真实控制流嵌套深度；JS/TS 保留关键字计数启发式。
        """
        if artifact.get("path", "").endswith(".py"):
            return self._check_nesting_depth_python(artifact, evidence_id)
        return self._check_nesting_depth_brace(artifact, evidence_id)

    def _check_nesting_depth_python(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """Python 嵌套深度：AST 栈遍历，统计控制流节点最大嵌套层级。"""
        findings: List[Finding] = []
        try:
            tree = ast.parse(artifact["content"])
        except SyntaxError:
            return findings

        max_depth = 0
        max_depth_line = 1
        stack: List[Tuple[Any, int]] = [(tree, 0)]

        while stack:
            node, depth = stack.pop()
            if isinstance(node, self._CONTROL_TYPES):
                depth += 1
                if depth > max_depth:
                    max_depth = depth
                    max_depth_line = getattr(node, "lineno", max_depth_line)
            for child in ast.iter_child_nodes(node):
                stack.append((child, depth))

        if max_depth > self.MAX_NESTING_DEPTH:
            findings.append(
                self._build_nesting_finding(
                    max_depth, max_depth_line, artifact["path"], evidence_id,
                )
            )
        return findings

    def _check_nesting_depth_brace(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """JS/TS 嵌套深度：关键字计数 + 花括号减层（保留启发式）。"""
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        max_depth = 0
        max_depth_line = 0
        current_depth = 0

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()

            if re.search(r'\b(if|for|while|switch|try|catch)\b', stripped):
                current_depth += 1
                if current_depth > max_depth:
                    max_depth = current_depth
                    max_depth_line = line_num

            if stripped == "}" or stripped == "})":
                current_depth = max(0, current_depth - 1)

        if max_depth > self.MAX_NESTING_DEPTH:
            findings.append(
                self._build_nesting_finding(
                    max_depth, max_depth_line, artifact["path"], evidence_id,
                )
            )
        return findings

    # ═══════════════════════════════════════════════════════════════
    # R18 参数过多
    # ═══════════════════════════════════════════════════════════════

    def _check_parameter_count(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测参数过多（R18）

        Python 用 AST 精确数参数（含位置、仅关键字、*args、**kwargs）；
        JS/TS 保留单行签名 split 启发式。
        """
        if artifact.get("path", "").endswith(".py"):
            return self._check_parameter_count_python(artifact, evidence_id)
        return self._check_parameter_count_brace(artifact, evidence_id)

    def _check_parameter_count_python(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """Python 参数计数：AST FunctionDef.args，含 vararg/kwarg/kwonlyargs。"""
        findings: List[Finding] = []
        try:
            tree = ast.parse(artifact["content"])
        except SyntaxError:
            return findings

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            count = len(args.args) + len(args.kwonlyargs)
            if args.vararg:
                count += 1
            if args.kwarg:
                count += 1
            if count > self.MAX_PARAMETERS:
                findings.append(
                    self._build_param_finding(
                        node.name, count, node.lineno, artifact["path"], evidence_id,
                    )
                )
        return findings

    def _check_parameter_count_brace(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """JS/TS 参数计数：单行签名逗号分割（保留启发式，多行签名已知局限）。"""
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()

            func_match = re.search(
                r'(?:function|def)\s+(\w+)\s*\(([^)]*)\)',
                stripped,
            )
            if func_match:
                func_name = func_match.group(1)
                params_str = func_match.group(2).strip()

                if params_str:
                    param_count = len([p for p in params_str.split(",") if p.strip()])
                    if param_count > self.MAX_PARAMETERS:
                        findings.append(
                            self._build_param_finding(
                                func_name, param_count, line_num,
                                artifact["path"], evidence_id,
                            )
                        )

        return findings

    # ═══════════════════════════════════════════════════════════════
    # Finding 构造
    # ═══════════════════════════════════════════════════════════════

    def _build_length_finding(
        self, function_name: str, function_length: int,
        line_start: int, line_end: int,
        file_path: str, evidence_id: str,
    ) -> Finding:
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="complexity",
            rule_id="R16",
            severity=Severity.WARNING,
            confidence=Confidence(
                value=0.85,
                kind=ConfidenceKind.HEURISTIC,
            ),
            title="函数过长",
            symptom=f"函数 '{function_name}' 包含 {function_length} 行，超过建议的 {self.MAX_FUNCTION_LENGTH} 行",
            source=FindingSource(
                evidence_id=evidence_id,
                file=file_path,
                line_start=line_start,
                line_end=line_end,
            ),
            consequence="函数过长难以理解和维护，容易隐藏 bug",
            remedy="拆分为多个小函数，每个函数聚焦单一职责",
        )

    def _build_nesting_finding(
        self, max_depth: int, max_depth_line: int,
        file_path: str, evidence_id: str,
    ) -> Finding:
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="complexity",
            rule_id="R17",
            severity=Severity.WARNING,
            confidence=Confidence(
                value=0.85,
                kind=ConfidenceKind.HEURISTIC,
            ),
            title="嵌套过深",
            symptom=f"代码嵌套层级为 {max_depth} 层，超过建议的 {self.MAX_NESTING_DEPTH} 层",
            source=FindingSource(
                evidence_id=evidence_id,
                file=file_path,
                line_start=max_depth_line,
                line_end=max_depth_line,
            ),
            consequence="嵌套过深导致代码难以理解和维护，增加认知负担",
            remedy="使用提前返回（early return）或提取辅助函数减少嵌套",
        )

    def _build_param_finding(
        self, func_name: str, param_count: int, line_num: int,
        file_path: str, evidence_id: str,
    ) -> Finding:
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="complexity",
            rule_id="R18",
            severity=Severity.INFO,
            confidence=Confidence(
                value=0.85,
                kind=ConfidenceKind.HEURISTIC,
            ),
            title="参数过多",
            symptom=f"函数 '{func_name}' 有 {param_count} 个参数，超过建议的 {self.MAX_PARAMETERS} 个",
            source=FindingSource(
                evidence_id=evidence_id,
                file=file_path,
                line_start=line_num,
                line_end=line_num,
            ),
            consequence="参数过多导致函数难以理解和使用，容易传错参数",
            remedy="使用对象/配置参数封装相关参数，或拆分函数",
        )
