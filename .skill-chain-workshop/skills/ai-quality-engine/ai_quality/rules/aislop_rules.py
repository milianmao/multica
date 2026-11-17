"""
ai_quality/rules/aislop_rules.py

AI Slop 反模式检测引擎

融合 aislop（github.com/scanaislop/aislop）的核心能力：
AI 生成代码特有反模式（AI Slop）的确定性检测。

对应数据流节点：I（Static Rules - AI Slop）

实现的规则：
- R19 叙述性注释：注释只是复述下方代码，无额外信息
- R20 调试打印：print() 出现在生产代码（非 __main__ / CLI 入口）
- R21 None 比较：Python 用 == None / != None 应改为 is None / is not None
- R22 未使用变量：函数内赋值后从未被读取

判定策略：Python AST + tokenize，确定性、零 LLM。仅 Python 生效。

信号分层：
- R21 None 比较   → confirmed-defect，confidence 0.85
- R22 未使用变量 → confirmed-defect，confidence 0.80
- R20 调试打印   → ai-slop-indicator，confidence 0.75
- R19 叙述性注释 → style-policy，confidence 0.60
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import uuid
from typing import Any, Dict, Iterator, List

from ai_quality.contracts.evaluation_contract import (
    Confidence,
    ConfidenceKind,
    Finding,
    FindingSource,
    Severity,
)
from ai_quality.rules.common import match_evidence_id


class AislopRulesEngine:
    """
    AI Slop 反模式检测引擎

    检测 AI 生成代码特有的反模式，确定性、零 LLM 调用。
    """

    # 指令型注释（不应被判为叙述性注释）
    _DIRECTIVE_WORDS = ("todo", "fixme", "xxx", "hack", "noqa", "type:", "pylint")

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行所有 AI Slop 规则

        Args:
            normalized_artifacts: 规范化后的 Artifact 列表
            evidence_manifest: Evidence Manifest

        Returns:
            Finding 列表
        """
        findings: List[Finding] = []

        for artifact in normalized_artifacts:
            if not artifact.get("path", "").endswith(".py"):
                continue  # 仅 Python
            evidence_id = self._find_evidence_id(artifact, evidence_manifest)
            content = artifact["content"]
            path = artifact["path"]

            findings.extend(self._check_narrative_comment(content, path, evidence_id))
            findings.extend(self._check_print_debug(content, path, evidence_id))
            findings.extend(self._check_none_comparison(content, path, evidence_id))
            findings.extend(self._check_unused_variable(content, path, evidence_id))

        return findings

    def _find_evidence_id(
        self, artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
    ) -> str:
        """根据 artifact path 精确匹配对应的 evidence_id"""
        return match_evidence_id(artifact, evidence_manifest)

    # ═══════════════════════════════════════════════════════════════
    # R19 叙述性注释
    # ═══════════════════════════════════════════════════════════════

    def _check_narrative_comment(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测叙述性注释（R19）

        注释文本中的标识符全部出现在下方代码里，且注释无额外信息，
        说明注释只是"复述"代码——AI 生成的典型冗余注释。
        """
        findings: List[Finding] = []
        lines = content.split("\n")

        comments: List[tuple] = []
        try:
            tokens = tokenize.generate_tokens(io.StringIO(content).readline)
            for tok in tokens:
                if tok.type == tokenize.COMMENT:
                    comments.append((tok.start[0], tok.string))
        except (tokenize.TokenError, IndentationError):
            return findings

        for lineno, text in comments:
            body = text.lstrip("#").strip()
            if not body or len(body) > 40:
                continue
            lowered = body.lower()
            if any(d in lowered for d in self._DIRECTIVE_WORDS):
                continue

            # 取下方第一个非空代码行
            next_line = ""
            for j in range(lineno, len(lines)):
                if lines[j].strip():
                    next_line = lines[j].strip()
                    break
            if not next_line:
                continue

            comment_ids = set(re.findall(r"[a-zA-Z_]\w*|\d+", body))
            code_ids = set(re.findall(r"[a-zA-Z_]\w*|\d+", next_line))
            # 注释标识符全部被代码覆盖 => 注释只是复述代码
            if comment_ids and comment_ids <= code_ids:
                findings.append(
                    self._build_finding(
                        file_path, lineno, evidence_id, "R19", Severity.INFO, 0.6,
                        "叙述性注释",
                        f"注释『{body}』只是复述下一行代码，无额外信息",
                        "冗余注释增加阅读负担，AI 生成的代码常带此类注释",
                        "删除复述代码的注释，或用注释说明'为什么'而非'是什么'",
                    )
                )
        return findings

    # ═══════════════════════════════════════════════════════════════
    # R20 调试打印
    # ═══════════════════════════════════════════════════════════════

    def _check_print_debug(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测调试打印（R20）

        print() 出现在非 __main__ / CLI 入口的代码中，通常是调试残留。
        """
        findings: List[Finding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        main_blocks = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.If) and self._is_name_main(n.test)
        ]

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not self._is_print_func(node.func):
                continue
            if main_blocks and self._in_any_ancestor(node, tree, main_blocks):
                continue  # 在 __main__ 块内，是 CLI 输出，不算残留
            findings.append(
                self._build_finding(
                    file_path, node.lineno, evidence_id, "R20", Severity.WARNING, 0.75,
                    "调试打印",
                    "print() 出现在生产代码中，可能是调试残留",
                    "调试输出残留会污染日志，且可能泄露敏感信息",
                    "移除 print 调试语句，改用 logging 模块",
                )
            )
        return findings

    @staticmethod
    def _is_name_main(test: Any) -> bool:
        return (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
        )

    @staticmethod
    def _is_print_func(func: Any) -> bool:
        if isinstance(func, ast.Name):
            return func.id == "print"
        if isinstance(func, ast.Attribute):
            return func.attr == "print"
        return False

    def _in_any_ancestor(self, node: Any, tree: Any, targets: List[Any]) -> bool:
        """判断 node 是否位于任一 target 节点内部（沿父链）。"""
        parents = self._build_parent_map(tree)
        cur = node
        while cur in parents:
            cur = parents[cur]
            if cur in targets:
                return True
        return False

    @staticmethod
    def _build_parent_map(tree: Any) -> Dict[Any, Any]:
        parents: Dict[Any, Any] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        return parents

    # ═══════════════════════════════════════════════════════════════
    # R21 None 比较
    # ═══════════════════════════════════════════════════════════════

    def _check_none_comparison(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测 == None / != None（R21）

        Python 中与 None 比较应使用 is / is not，而非 == / !=。
        """
        findings: List[Finding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and self._is_none(comp):
                    findings.append(
                        self._build_finding(
                            file_path, node.lineno, evidence_id, "R21",
                            Severity.WARNING, 0.85,
                            "None 比较",
                            "使用 == / != 与 None 比较，应使用 is / is not",
                            "== 走 __eq__，is 才比较对象身份；与 None 比较应使用 is",
                            "将 'x == None' 改为 'x is None'，'x != None' 改为 'x is not None'",
                        )
                    )
                    break
        return findings

    @staticmethod
    def _is_none(node: Any) -> bool:
        return isinstance(node, ast.Constant) and node.value is None

    # ═══════════════════════════════════════════════════════════════
    # R22 未使用变量
    # ═══════════════════════════════════════════════════════════════

    def _check_unused_variable(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测未使用变量（R22）

        函数体内赋值（Store）但从未被读取（Load）的局部变量。
        豁免：下划线开头、函数参数、解构赋值（多目标）。
        """
        findings: List[Finding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            args = self._collect_arg_names(node)
            stored: set = set()
            loaded: set = set()
            # 跳过嵌套函数体的名字（闭包/内部作用域）
            for name_node in self._iter_names_excluding_nested(node):
                if isinstance(name_node.ctx, ast.Store):
                    stored.add(name_node.id)
                elif isinstance(name_node.ctx, ast.Load):
                    loaded.add(name_node.id)

            unused = stored - loaded - args
            for name in sorted(unused):
                if name.startswith("_"):
                    continue
                line = self._find_store_line(node, name)
                findings.append(
                    self._build_finding(
                        file_path, line, evidence_id, "R22", Severity.WARNING, 0.8,
                        "未使用变量",
                        f"变量 '{name}' 在函数 '{node.name}' 内赋值后从未被读取",
                        "未使用的变量是死代码，增加阅读负担且可能掩盖逻辑错误",
                        "删除未使用的变量赋值，或检查是否遗漏了对它的使用",
                    )
                )
        return findings

    @staticmethod
    def _collect_arg_names(func: Any) -> set:
        args = {a.arg for a in func.args.args} | {a.arg for a in func.args.kwonlyargs}
        if func.args.vararg:
            args.add(func.args.vararg.arg)
        if func.args.kwarg:
            args.add(func.args.kwarg.arg)
        return args

    @classmethod
    def _iter_names_excluding_nested(cls, func: Any) -> Iterator[Any]:
        """迭代函数体内所有 Name，跳过嵌套函数/lambda 子树。"""
        for child in ast.iter_child_nodes(func):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Name):
                yield child
            yield from cls._iter_names_excluding_nested(child)

    @staticmethod
    def _find_store_line(func: Any, name: str) -> int:
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        return node.lineno
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == name:
                    return node.lineno
        return func.lineno

    # ═══════════════════════════════════════════════════════════════
    # Finding 构造
    # ═══════════════════════════════════════════════════════════════

    def _build_finding(
        self, file_path: str, line: int, evidence_id: str,
        rule_id: str, severity: Severity, confidence: float,
        title: str, symptom: str, consequence: str, remedy: str,
    ) -> Finding:
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="aislop",
            rule_id=rule_id,
            severity=severity,
            confidence=Confidence(
                value=confidence,
                kind=ConfidenceKind.HEURISTIC,
            ),
            title=title,
            symptom=symptom,
            source=FindingSource(
                evidence_id=evidence_id,
                file=file_path,
                line_start=line,
                line_end=line,
            ),
            consequence=consequence,
            remedy=remedy,
        )
