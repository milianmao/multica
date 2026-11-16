"""
backend/rules/static_rules.py

静态规则引擎 - 实现 code-static 的静态检查规则

对应数据流节点：I（Static Rules）

实现的规则：
- R01: 文件长度检查（超过 50 行警告）
- R02: 异常吞噬检查（空 catch 块）
- R03: 未使用导入检查（简化版，预留扩展）
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


class StaticRulesEngine:
    """
    静态规则引擎

    执行 code-static 的静态检查规则，每条规则输出：
    - Rule ID
    - 位置（file + line range）
    - 严重度
    - 启发式置信度
    - Evidence 引用
    """

    # 规则配置
    # 文件长度阈值：基于真实项目文件行数分布校准（P90 ≈ 370 行），
    # 只报真正异常的超大文件，避免对常规文件全量误报
    MAX_FILE_LENGTH = 300

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行所有静态规则

        Args:
            normalized_artifacts: 规范化后的 Artifact 列表
            evidence_manifest: Evidence Manifest（用于获取 evidence_id）

        Returns:
            Finding 列表
        """
        findings: List[Finding] = []

        for artifact in normalized_artifacts:
            # 获取当前 artifact 对应的 evidence_id
            evidence_id = self._find_evidence_id(artifact, evidence_manifest)

            # 规则 1: 文件长度检查
            findings.extend(self._check_function_length(artifact, evidence_id))

            # 规则 2: 异常吞噬检查
            findings.extend(self._check_exception_swallowing(artifact, evidence_id))

            # 规则 3: 未使用导入检查（简化版，预留扩展）
            findings.extend(self._check_unused_imports(artifact, evidence_id))

        return findings

    def _find_evidence_id(
        self, artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
    ) -> str:
        """
        根据 artifact path 精确匹配对应的 evidence_id

        Args:
            artifact: Artifact 字典
            evidence_manifest: Evidence Manifest

        Returns:
            evidence_id，未匹配到则回退到第一条 evidence 或占位符
        """
        return match_evidence_id(artifact, evidence_manifest)

    def _check_function_length(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检查文件长度（超过 50 行警告）

        简化实现：检测整个文件行数。
        实际生产实现应使用 AST 解析检测单个函数长度。

        Args:
            artifact: Artifact 字典
            evidence_id: 关联的 Evidence ID

        Returns:
            Finding 列表
        """
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")
        file_length = len(lines)

        if file_length > self.MAX_FILE_LENGTH:
            findings.append(
                Finding(
                    finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                    dimension="maintainability",
                    rule_id="R01",
                    severity=Severity.WARNING,
                    confidence=Confidence(
                        value=0.7,
                        kind=ConfidenceKind.HEURISTIC,
                    ),
                    title="文件过长",
                    symptom=f"文件包含 {file_length} 行，超过建议的 {self.MAX_FILE_LENGTH} 行",
                    source=FindingSource(
                        evidence_id=evidence_id,
                        file=artifact["path"],
                        line_start=1,
                        line_end=file_length,
                    ),
                    consequence="文件过长会增加理解和维护难度",
                    remedy="考虑拆分文件，每个文件聚焦单一职责",
                )
            )

        return findings

    def _check_exception_swallowing(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检查空 catch/except 块（异常吞噬）

        判定按文件语言分流：
        - Python：AST 解析 except 块体，仅当块体只有 pass/.../空（注释不产生 AST 节点）
          时才报；块体含日志、raise、return、赋值等实际语句则豁免。
        - JS/TS/Java：花括号配对提取 catch 块体，仅当块体只有空白/注释时才报。

        Args:
            artifact: Artifact 字典
            evidence_id: 关联的 Evidence ID

        Returns:
            Finding 列表
        """
        content = artifact["content"]
        path = artifact.get("path", "")

        if path.endswith(".py"):
            return self._check_python_exception_swallowing(content, path, evidence_id)
        return self._check_brace_catch_swallowing(content, path, evidence_id)

    def _check_python_exception_swallowing(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """Python 异常吞噬：AST 判断 except 块体是否真空（仅 pass/.../空）。"""
        findings: List[Finding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            # 无法解析则不做判定，避免在语法错误文件上产生噪音
            return findings

        lines = content.split("\n")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not self._is_blank_except_body(node.body):
                continue
            # 显式豁免：except 声明行带 # noqa
            if node.lineno and "# noqa" in lines[node.lineno - 1]:
                continue
            findings.append(
                self._build_swallowing_finding(
                    file_path, node.lineno, node.end_lineno, evidence_id
                )
            )
        return findings

    @staticmethod
    def _is_blank_except_body(body: List[Any]) -> bool:
        """except 块体是否为空：所有语句都是 pass 或 ...（注释不产生 AST 节点）。"""
        if not body:
            return True
        for stmt in body:
            if isinstance(stmt, ast.Pass):
                continue
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and stmt.value.value is Ellipsis
            ):
                continue
            return False
        return True

    def _check_brace_catch_swallowing(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """JS/TS/Java 异常吞噬：花括号配对提取 catch 块体，仅空白/注释时报警。"""
        findings: List[Finding] = []
        catch_pattern = re.compile(r"catch\s*\([^)]*\)\s*\{")
        for match in catch_pattern.finditer(content):
            open_idx = content.find("{", match.start())
            close_idx = self._find_matching_brace(content, open_idx)
            if close_idx == -1:
                continue
            body = content[open_idx + 1 : close_idx]
            if not self._body_is_blank(body):
                continue
            line_num = content[: match.start()].count("\n") + 1
            findings.append(
                self._build_swallowing_finding(file_path, line_num, line_num, evidence_id)
            )
        return findings

    @staticmethod
    def _find_matching_brace(content: str, open_idx: int) -> int:
        """从 open_idx 的 '{' 出发，返回配对的 '}' 位置；未闭合返回 -1。"""
        depth = 0
        for i in range(open_idx, len(content)):
            ch = content[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        return -1

    @staticmethod
    def _body_is_blank(body: str) -> bool:
        """catch 块体是否只有空白/注释（去掉 // 行注释与 /* */ 块注释后为空）。"""
        cleaned = re.sub(r"//[^\n]*", "", body)
        cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
        return not cleaned.strip()

    def _build_swallowing_finding(
        self, file_path: str, line_start: int, line_end: int, evidence_id: str
    ) -> Finding:
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="reliability",
            rule_id="R02",
            severity=Severity.ERROR,
            confidence=Confidence(
                value=0.95,
                kind=ConfidenceKind.HEURISTIC,
            ),
            title="异常吞噬",
            symptom="catch/except 块为空，异常被静默忽略",
            source=FindingSource(
                evidence_id=evidence_id,
                file=file_path,
                line_start=line_start,
                line_end=line_end,
            ),
            consequence="隐藏错误，导致问题难以调试",
            remedy="至少记录日志，或向上抛出异常",
        )

    def _check_unused_imports(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检查未使用导入（R03）

        Python 用 AST：收集 import / from-import 的名字，检查是否在文件其余部分被引用。
        未引用且非下划线开头则报；import * 跳过（无法静态判定）。JS/TS 无 AST 依赖，返回空。

        已知局限：字符串形式的动态引用（如 globals()["name"]）无法静态识别，可能误报。

        Args:
            artifact: Artifact 字典
            evidence_id: 关联的 Evidence ID

        Returns:
            Finding 列表
        """
        path = artifact.get("path", "")
        # __init__.py 的 import 通常为 re-export（对外转发），跳过避免误报
        if not path.endswith(".py") or path.endswith("__init__.py"):
            return []
        content = artifact["content"]
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        findings: List[Finding] = []
        imports: Dict[str, int] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("*"):
                        continue
                    name = alias.asname or alias.name.split(".")[0]
                    imports[name] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                # from __future__ import X 是编译指令，不是普通导入
                if node.module == "__future__":
                    continue
                if any(a.name == "*" for a in node.names):
                    continue
                for alias in node.names:
                    imports[alias.asname or alias.name] = node.lineno

        # 收集文件中所有 Name 引用（含属性访问的根，如 os.path.join → os）
        used: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)

        # 收集 __all__ 显式导出（re-export 约定，即使文件内部未用也不算未使用导入）
        exports: set = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "__all__"
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        exports.add(elt.value)

        for name, line in sorted(imports.items(), key=lambda kv: kv[1]):
            if name in used or name.startswith("_") or name in exports:
                continue
            findings.append(
                Finding(
                    finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                    dimension="maintainability",
                    rule_id="R03",
                    severity=Severity.WARNING,
                    confidence=Confidence(
                        value=0.8,
                        kind=ConfidenceKind.HEURISTIC,
                    ),
                    title="未使用导入",
                    symptom=f"导入 '{name}' 在文件中未被使用",
                    source=FindingSource(
                        evidence_id=evidence_id,
                        file=artifact["path"],
                        line_start=line,
                        line_end=line,
                    ),
                    consequence="未使用的导入增加维护负担，且可能引入无意义依赖",
                    remedy="删除未使用的导入语句",
                )
            )

        return findings
