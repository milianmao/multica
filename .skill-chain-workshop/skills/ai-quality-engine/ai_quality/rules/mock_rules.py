"""
ai_quality/rules/mock_rules.py

Mock 代码深度检测引擎

痛点背景：AI Coding 规划多个版本后，交付代码可能是 mock/stub/占位实现——
藏得很深，外部全链路测试（黑盒）看不出来。本引擎在代码静态层识别伪实现痕迹。

对应数据流节点：I（Static Rules - Mock）

实现的规则：
- R23 硬编码数据返回：函数体恒返回固定 dict/list/str，无真实逻辑
- R24 mock 命名残留：标识符含 mock_/fake/stub/dummy/placeholder（非测试目录）
- R25 空实现桩：函数体仅 pass/.../return None/return 常量（非 __main__/抽象方法）
- R26 测试 Mock 泄漏：生产代码 import 测试专用 mock 库（unittest.mock/mocker/monkeypatch）

判定策略：Python AST，确定性、零 LLM。仅 Python 生效。

信号分层：
- R23 硬编码数据返回 → confirmed-defect，confidence 0.80
- R24 mock 命名残留   → ai-slop-indicator，confidence 0.75
- R25 空实现桩       → confirmed-defect，confidence 0.78
- R26 测试 Mock 泄漏 → ai-slop-indicator，confidence 0.60
"""

from __future__ import annotations

import ast
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

# Mock/占位命名关键词（作为标识符片段匹配）
_MOCK_NAME_TOKENS = ("mock", "fake", "stub", "dummy", "placeholder")

# 测试专用 mock 库（import 匹配，生产代码不应引入）
_MOCK_TEST_LIBS = ("unittest.mock", "unittest", "mocker", "monkeypatch")

# 测试目录路径片段（R24/R26 豁免）
_TEST_PATH_SEGMENTS = ("test_", "tests/", "_test.py", "/test/", "spec/")


class MockRulesEngine:
    """
    Mock 代码深度检测引擎

    检测 AI Coding 交付代码中的 mock/stub/占位实现，确定性、零 LLM 调用。
    """

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行所有 Mock 规则

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

            findings.extend(self._check_hardcoded_return(content, path, evidence_id))
            findings.extend(self._check_mock_naming(content, path, evidence_id))
            findings.extend(self._check_empty_stub(content, path, evidence_id))
            findings.extend(self._check_test_mock_leak(content, path, evidence_id))

        return findings

    def _find_evidence_id(
        self, artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
    ) -> str:
        """根据 artifact path 精确匹配对应的 evidence_id"""
        return match_evidence_id(artifact, evidence_manifest)

    @staticmethod
    def _is_test_path(path: str) -> bool:
        """判断文件是否属于测试目录（R24/R26 豁免）"""
        return any(seg in path for seg in _TEST_PATH_SEGMENTS)

    # ═══════════════════════════════════════════════════════════════
    # R23 硬编码数据返回
    # ═══════════════════════════════════════════════════════════════

    def _check_hardcoded_return(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测硬编码数据返回（R23）

        函数体恒返回固定 dict/list/str（与参数无关、无副作用），
        说明函数没有真实逻辑，是 mock 伪实现。
        """
        findings: List[Finding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        main_blocks = self._find_main_blocks(tree)
        if file_path.endswith("__init__.py"):
            return findings  # __init__.py 通常是常量导出，豁免

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if self._in_any_ancestor(node, tree, main_blocks):
                continue  # __main__ 块内（脚本逻辑），豁免

            returns = list(self._iter_returns_excluding_nested(node))
            if not returns:
                continue

            # 函数体不得有副作用语句（赋值/调用/控制流）
            if self._has_side_effect_statements(node):
                continue

            # 至少一个 return 是非 None 的复杂字面量
            has_complex_literal = False
            for ret in returns:
                if ret.value is not None and self._is_complex_literal(ret.value):
                    has_complex_literal = True
                    break
            if not has_complex_literal:
                continue

            findings.append(
                self._build_finding(
                    file_path, node.lineno, evidence_id, "R23",
                    Severity.WARNING, 0.8,
                    "硬编码数据返回",
                    f"函数 '{node.name}' 恒返回固定字面量，无真实逻辑",
                    "函数不依赖输入参数计算，返回写死的 mock 数据，外部全链路测试无法察觉",
                    "实现真实逻辑（读取数据源/计算/调用服务），或标记为显式 TODO 数据",
                )
            )
        return findings

    @staticmethod
    def _is_complex_literal(node: Any) -> bool:
        """判断节点是否为非 None 的复杂字面量（dict/list/set/tuple/长字符串/数字）"""
        if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
            return True
        if isinstance(node, ast.Constant):
            # 非 None 且字符串非空
            if node.value is None:
                return False
            if isinstance(node.value, str):
                return len(node.value) > 0
            return True
        return False

    @classmethod
    def _has_side_effect_statements(cls, func: Any) -> bool:
        """判断函数体（不含嵌套函数）是否含副作用语句（赋值/调用/控制流/IO）"""
        for child in ast.iter_child_nodes(func):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign,
                                 ast.If, ast.For, ast.While,
                                 ast.With, ast.Try, ast.AsyncFor, ast.AsyncWith)):
                return True
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                return True
            if cls._has_side_effect_statements(child):
                return True
        return False

    @classmethod
    def _iter_returns_excluding_nested(cls, func: Any) -> Iterator[Any]:
        """迭代函数体内所有 Return，跳过嵌套函数/lambda 子树"""
        for child in ast.iter_child_nodes(func):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return):
                yield child
            yield from cls._iter_returns_excluding_nested(child)

    # ═══════════════════════════════════════════════════════════════
    # R24 mock 命名残留
    # ═══════════════════════════════════════════════════════════════

    def _check_mock_naming(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测 mock 命名残留（R24）

        标识符（函数/变量/类名）含 mock_/fake/stub/dummy/placeholder，
        通常是 AI 规划多版本后的 mock 命名残留。测试目录豁免。
        """
        findings: List[Finding] = []
        if self._is_test_path(file_path):
            return findings

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        seen: set = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                line = node.lineno
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                name = node.id
                line = node.lineno
            else:
                continue

            if name in seen:
                continue
            seen.add(name)

            if self._is_mock_name(name):
                findings.append(
                    self._build_finding(
                        file_path, line, evidence_id, "R24",
                        Severity.WARNING, 0.75,
                        "Mock 命名残留",
                        f"标识符 '{name}' 含 mock/占位命名特征",
                        "mock 命名的标识符是 AI 规划多版本后的残留，可能是未实现的占位",
                        "重命名为真实语义（如 mock_service → user_service），或确认实现完成",
                    )
                )
        return findings

    @staticmethod
    def _is_mock_name(name: str) -> bool:
        """判断标识符是否含 mock 命名特征"""
        lower = name.lower()
        for tok in _MOCK_NAME_TOKENS:
            if lower == tok:
                return True
            if lower.startswith(tok + "_"):
                return True
            if "_" + tok + "_" in lower or lower.endswith("_" + tok):
                return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # R25 空实现桩
    # ═══════════════════════════════════════════════════════════════

    def _check_empty_stub(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测空实现桩（R25）

        函数体仅 pass/.../return None/return 常量——AI 交付的空壳函数。
        豁免：抽象方法（抛 NotImplementedError）、__init__ 全 pass、__main__ 内。
        """
        findings: List[Finding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        main_blocks = self._find_main_blocks(tree)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if self._in_any_ancestor(node, tree, main_blocks):
                continue

            # 抽象方法：函数体抛 NotImplementedError → 豁免
            if self._raises_not_implemented(node):
                continue
            # __init__ 全 pass → 可接受的空构造
            if node.name == "__init__" and self._is_pure_empty(node, allow_docstring=True):
                continue
            # 纯空实现（允许 docstring）→ 命中；但详细接口说明（≥30 字符）豁免
            if self._is_pure_empty(node, allow_docstring=True):
                if self._has_detailed_docstring(node):
                    continue  # 详细接口契约 → 豁免
                findings.append(
                    self._build_finding(
                        file_path, node.lineno, evidence_id, "R25",
                        Severity.WARNING, 0.78,
                        "空实现桩",
                        f"函数 '{node.name}' 是空实现（仅 pass/return 常量）",
                        "函数无真实逻辑，AI 交付的空壳，外部调用会静默返回空值或 None",
                        "实现函数逻辑，或抛 NotImplementedError 明确未实现",
                    )
                )
        return findings

    @staticmethod
    def _has_docstring(func: Any) -> bool:
        """判断函数是否有 docstring"""
        body = func.body
        return bool(body) and isinstance(body[0], ast.Expr) and isinstance(
            body[0].value, ast.Constant
        ) and isinstance(body[0].value.value, str)

    @classmethod
    def _has_detailed_docstring(cls, func: Any) -> bool:
        """
        判断函数是否有详细接口说明 docstring（≥30 字符）

        短 docstring（如 "命名不规范"、"测试"、"示例"）是 AI mock 的敷衍注释，
        不豁免；详细接口契约（说明参数/返回/行为）视为显式接口，豁免。
        """
        body = func.body
        if not body or not isinstance(body[0], ast.Expr):
            return False
        expr = body[0].value
        if not isinstance(expr, ast.Constant) or not isinstance(expr.value, str):
            return False
        return len(expr.value.strip()) >= 30

    @classmethod
    def _is_pure_empty(cls, func: Any, allow_docstring: bool) -> bool:
        """判断函数体是否为空实现（仅 pass/.../return 简单常量）"""
        body = func.body
        if allow_docstring and cls._has_docstring(func):
            body = body[1:]
        if not body:
            return True
        for stmt in body:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                if stmt.value.value is Ellipsis:
                    continue
            if isinstance(stmt, ast.Return):
                if stmt.value is None:
                    continue
                # return None/简单常量
                if isinstance(stmt.value, ast.Constant) and (
                    stmt.value.value is None or stmt.value.value in (True, False, 0, "")
                ):
                    continue
            return False
        return True

    @staticmethod
    def _raises_not_implemented(func: Any) -> bool:
        """判断函数体是否只抛 NotImplementedError"""
        body = func.body
        return bool(body) and all(
            isinstance(s, ast.Raise) and isinstance(s.exc, ast.Call)
            and isinstance(s.exc.func, ast.Name)
            and s.exc.func.id == "NotImplementedError"
            for s in body
        )

    # ═══════════════════════════════════════════════════════════════
    # R26 测试 Mock 泄漏
    # ═══════════════════════════════════════════════════════════════

    def _check_test_mock_leak(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测测试 Mock 泄漏（R26）

        生产代码 import 测试专用 mock 库（unittest.mock/mocker/monkeypatch），
        说明生产逻辑可能被 mock 依赖替代。测试目录豁免。
        """
        findings: List[Finding] = []
        if self._is_test_path(file_path):
            return findings

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        for node in ast.walk(tree):
            module = None
            line = 0
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if self._is_mock_test_lib(alias.name):
                        module, line = alias.name, node.lineno
                        break
            elif isinstance(node, ast.ImportFrom) and node.module:
                if self._is_mock_test_lib(node.module):
                    module, line = node.module, node.lineno
            if not module:
                continue

            findings.append(
                self._build_finding(
                    file_path, line, evidence_id, "R26",
                    Severity.INFO, 0.6,
                    "测试 Mock 泄漏到生产",
                    f"生产代码 import 了测试专用 mock 库 '{module}'",
                    "生产代码依赖测试 mock 库，说明真实依赖可能被 mock 替代，全链路测试无法验证真实行为",
                    "移除生产代码中的 mock 依赖，使用真实实现或依赖注入",
                )
            )
        return findings

    @staticmethod
    def _is_mock_test_lib(module: str) -> bool:
        """判断模块名是否为测试专用 mock 库"""
        for lib in _MOCK_TEST_LIBS:
            if module == lib or module.startswith(lib + "."):
                return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # 公共辅助
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _find_main_blocks(tree: Any) -> List[Any]:
        """收集所有 if __name__ == '__main__' 块"""
        return [
            n for n in ast.walk(tree)
            if isinstance(n, ast.If) and MockRulesEngine._is_name_main(n.test)
        ]

    @staticmethod
    def _is_name_main(test: Any) -> bool:
        return (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
        )

    def _in_any_ancestor(self, node: Any, tree: Any, targets: List[Any]) -> bool:
        """判断 node 是否位于任一 target 节点内部（沿父链）"""
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
    # Finding 构造
    # ═══════════════════════════════════════════════════════════════

    def _build_finding(
        self, file_path: str, line: int, evidence_id: str,
        rule_id: str, severity: Severity, confidence: float,
        title: str, symptom: str, consequence: str, remedy: str,
    ) -> Finding:
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="mock",
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
