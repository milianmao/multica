"""
backend/rules/correctness_rules.py

正确性规则引擎 - 实现 correctness 维度的静态检查规则

对应数据流节点：I（Static Rules - Correctness）

实现的规则：
- R07: 空指针风险检测
- R08: 类型错误检测（隐式类型转换）
- R09: 死代码检测
"""

from __future__ import annotations

import ast
import re
import uuid
from typing import Any, Dict, List

from ai_quality.contracts.evaluation_contract import (
    Confidence,
    ConfidenceKind,
    Finding,
    FindingSource,
    Severity,
)
from ai_quality.rules.common import match_evidence_id


class CorrectnessRulesEngine:
    """
    正确性规则引擎

    执行 correctness 维度的静态检查规则，检测常见逻辑错误。
    """

    # 空指针风险模式（链式调用）
    NULL_POINTER_PATTERNS = [
        r'\w+\.\w+\.\w+',  # obj.prop.subProp
        r'\w+\?\.\w+\.\w+',  # obj?.prop.subProp（可选链后继续访问）
    ]

    # 隐式类型转换模式
    IMPLICIT_COERCION_PATTERNS = [
        r'[^!=]==[^=]',  # == 而非 ===
        r'![^!=]=[^=]',  # != 而非 !==
    ]

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行所有正确性规则

        Args:
            normalized_artifacts: 规范化后的 Artifact 列表
            evidence_manifest: Evidence Manifest

        Returns:
            Finding 列表
        """
        findings: List[Finding] = []

        for artifact in normalized_artifacts:
            evidence_id = self._find_evidence_id(artifact, evidence_manifest)

            # 规则 7: 空指针风险检测
            findings.extend(self._check_null_pointer(artifact, evidence_id))

            # 规则 8: 类型错误检测
            findings.extend(self._check_type_coercion(artifact, evidence_id))

            # 规则 9: 死代码检测
            findings.extend(self._check_dead_code(artifact, evidence_id))

        return findings

    def _find_evidence_id(
        self, artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
    ) -> str:
        """根据 artifact path 精确匹配对应的 evidence_id"""
        return match_evidence_id(artifact, evidence_manifest)

    def _check_null_pointer(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测空指针风险（R07）

        检测模式：
        - obj.prop.subProp（链式调用，中间可能为 null/undefined）
        """
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        # 只检测较长的链式调用（3层及以上）
        chain_pattern = r'\b\w+(?:\.\w+){3,}\b'

        for line_num, line in enumerate(lines, 1):
            matches = re.finditer(chain_pattern, line)
            for match in matches:
                # 排除常见的非风险模式
                matched = match.group()
                if any(safe in matched for safe in ["console.log", "Math.", "Object.", "Array.", "String.", "Number.", "JSON.", "Promise."]):
                    continue

                findings.append(
                    Finding(
                        finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                        dimension="correctness",
                        rule_id="R07",
                        severity=Severity.WARNING,
                        confidence=Confidence(
                            value=0.75,
                            kind=ConfidenceKind.HEURISTIC,
                        ),
                        title="空指针风险",
                        symptom=f"链式调用 '{matched}' 可能访问 null/undefined 对象的属性",
                        source=FindingSource(
                            evidence_id=evidence_id,
                            file=artifact["path"],
                            line_start=line_num,
                            line_end=line_num,
                        ),
                        consequence="运行时抛出 TypeError 或 NullPointerException，导致程序崩溃",
                        remedy="使用可选链操作符（?.）或提前进行 null 检查",
                    )
                )

        return findings

    def _check_type_coercion(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测隐式类型转换（R08）

        检测模式：
        - == 而非 ===
        - != 而非 !==
        """
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        # 语言门控：Python 无 ===，== 是唯一合法相等比较，跳过 .py 文件
        if artifact.get("path", "").endswith(".py"):
            return findings

        for line_num, line in enumerate(lines, 1):
            # 跳过注释行
            if line.strip().startswith("//") or line.strip().startswith("#"):
                continue

            # 检测 ==
            if re.search(r'[^!=]==[^=]', line):
                findings.append(
                    Finding(
                        finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                        dimension="correctness",
                        rule_id="R08",
                        severity=Severity.WARNING,
                        confidence=Confidence(
                            value=0.7,
                            kind=ConfidenceKind.HEURISTIC,
                        ),
                        title="隐式类型转换",
                        symptom="使用 == 而非 === 进行比较",
                        source=FindingSource(
                            evidence_id=evidence_id,
                            file=artifact["path"],
                            line_start=line_num,
                            line_end=line_num,
                        ),
                        consequence="可能导致意外的类型转换（如 0 == '' 为 true），引发逻辑错误",
                        remedy="使用严格相等运算符 === 和 !==",
                    )
                )

        return findings

    def _check_dead_code(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测死代码（R09）

        Python 用 AST：同一语句块内 return/raise/break/continue 之后的兄弟语句
        不可达，报死代码；跳过 try 块（except/finally 仍可能执行）。
        JS/TS 无 AST 依赖，返回空（原花括号启发式误报率高，如 if-return 后
        的正常代码会被误判为死代码）。

        Args:
            artifact: Artifact 字典
            evidence_id: 关联的 Evidence ID

        Returns:
            Finding 列表
        """
        if not artifact.get("path", "").endswith(".py"):
            return []
        content = artifact["content"]
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        findings: List[Finding] = []
        dead_types = (ast.Return, ast.Raise, ast.Break, ast.Continue)

        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                continue  # except/finally 仍可能执行，不判死代码
            body = getattr(node, "body", None)
            if not isinstance(body, list) or len(body) < 2:
                continue
            for i, stmt in enumerate(body[:-1]):
                if isinstance(stmt, dead_types):
                    for dead in body[i + 1 :]:
                        if isinstance(dead, ast.Pass):
                            continue
                        findings.append(
                            Finding(
                                finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                                dimension="correctness",
                                rule_id="R09",
                                severity=Severity.INFO,
                                confidence=Confidence(
                                    value=0.75,
                                    kind=ConfidenceKind.HEURISTIC,
                                ),
                                title="死代码",
                                symptom=f"第 {dead.lineno} 行位于 return/raise/break/continue 之后，不可达",
                                source=FindingSource(
                                    evidence_id=evidence_id,
                                    file=artifact["path"],
                                    line_start=dead.lineno,
                                    line_end=getattr(dead, "end_lineno", dead.lineno),
                                ),
                                consequence="代码永远不会执行，增加维护负担",
                                remedy="删除死代码或检查逻辑是否正确",
                            )
                        )
                    break

        return findings
