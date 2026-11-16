"""
ai_quality/rules/assertion_rules.py

断言质量检测引擎

痛点背景：AI Coding 产出的测试代码经常存在"假测试"——断言恒真、
函数无断言、断言写死魔法值、测试间共享可变状态。这些测试无法捕获
回归，让全链路验证形同虚设。本引擎在测试文件静态层识别断言质量问题。

对应数据流节点：I（Static Rules - Test Quality）

实现的规则：
- R27 弱断言：assert x is not None（排除后跟属性访问）/ assert True/False /
              assert len(x) >= 0 / assert x == x / assertTrue(True)/assertFalse(False)
- R28 无断言测试函数：def test_* 无任何断言语句（排除 conftest.py + 非 test_ 前缀函数）
- R29 魔法值断言：assert x == <literal> 且文件无对应常量定义（排除 0/1/""/None/True/False）
- R30 测试间共享可变状态：模块级 list/dict/set 被 test 写且无 teardown/fixture 清理

判定策略：Python AST，确定性、零 LLM。仅 Python 测试文件生效。

信号分层：
- R27 弱断言       → test-quality，warning，confidence 0.85
- R28 无断言       → test-quality，error，confidence 0.90
- R29 魔法值断言   → test-quality，warning，confidence 0.80
- R30 共享可变状态 → test-quality，warning，confidence 0.85
"""

from __future__ import annotations

import ast
import uuid
from typing import Any, Dict, Iterator, List, Optional

from ai_quality.contracts.evaluation_contract import (
    Confidence,
    ConfidenceKind,
    Finding,
    FindingSource,
    Severity,
)
from ai_quality.rules.common import match_evidence_id

# 共享可变状态的写操作方法（R30 识别）
_MUTATION_METHODS = (
    "append", "extend", "insert", "pop", "remove",
    "add", "update", "discard", "clear",
    "difference_update", "symmetric_difference_update",
)

# R29 豁免的常见基准字面量
_BASELINE_LITERALS = (0, 1, "", None, True, False)


class AssertionRulesEngine:
    """
    断言质量检测引擎

    检测 AI Coding 产出测试代码中的断言质量问题，确定性、零 LLM 调用。
    只处理测试文件（test_*.py / *_test.py 或路径含 test/）。
    """

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行所有断言质量规则

        Args:
            normalized_artifacts: 规范化后的 Artifact 列表
            evidence_manifest: Evidence Manifest

        Returns:
            Finding 列表
        """
        findings: List[Finding] = []

        # 预扫描 conftest.py 是否具备清理能力（R30 跨文件判定）
        conftest_cleanup = self._any_conftest_has_cleanup(normalized_artifacts)

        for artifact in normalized_artifacts:
            path = artifact.get("path", "")
            if not path.endswith(".py"):
                continue  # 仅 Python
            if not self._is_test_file(path):
                continue  # 非测试文件跳过
            evidence_id = self._find_evidence_id(artifact, evidence_manifest)
            content = artifact["content"]

            findings.extend(self._check_weak_assertions(content, path, evidence_id))            # R27
            findings.extend(self._check_no_assertions(content, path, evidence_id))              # R28
            findings.extend(self._check_magic_value_assertions(content, path, evidence_id))     # R29
            findings.extend(
                self._check_shared_mutable_state(content, path, evidence_id, conftest_cleanup)  # R30
            )

        return findings

    def _find_evidence_id(
        self, artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
    ) -> str:
        """根据 artifact path 精确匹配对应的 evidence_id"""
        return match_evidence_id(artifact, evidence_manifest)

    @staticmethod
    def _is_test_file(path: str) -> bool:
        """
        判断文件是否属于测试文件（test_*.py / *_test.py / 路径含 test/）

        非测试文件整文件跳过，避免对生产代码误报。
        """
        base = path.rsplit("/", 1)[-1]
        if base.startswith("test_"):
            return True
        if base.endswith("_test.py"):
            return True
        segments = path.split("/")
        return "test" in segments or "tests" in segments

    # ═══════════════════════════════════════════════════════════════
    # R27 弱断言
    # ═══════════════════════════════════════════════════════════════

    def _check_weak_assertions(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测弱断言（R27）

        恒真的断言模式：assert x is not None（无后续属性访问）、assert True/False、
        assert len(x) >= 0、assert x == x、assertTrue(True)/assertFalse(False)。
        """
        findings: List[Finding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        parents = self._build_parent_map(tree)

        for func in self._iter_test_functions(tree):
            for node in self._iter_stmts_excluding_nested(func):
                reason = None
                if isinstance(node, ast.Assert):
                    reason = self._weak_assert_reason(node, parents)
                elif isinstance(node, ast.Call):
                    reason = self._weak_assert_call_reason(node)
                if not reason:
                    continue
                findings.append(
                    self._build_finding(
                        file_path, node.lineno, evidence_id, "R27",
                        Severity.WARNING, 0.85,
                        "弱断言",
                        f"断言『{reason}』恒真，未验证任何真实行为",
                        "恒真的断言永远通过，无法捕获回归，测试形同虚设",
                        "改为验证具体值/状态（如断言返回值、副作用、异常），或删除该断言",
                    )
                )
        return findings

    @classmethod
    def _weak_assert_reason(cls, node: Any, parents: Dict[Any, Any]) -> Optional[str]:
        """识别 assert 语句是否为恒真弱断言，返回原因字符串；非弱断言返回 None"""
        test = node.test
        # assert True / assert False
        if isinstance(test, ast.Constant) and isinstance(test.value, bool):
            return f"assert {test.value}"
        if not isinstance(test, ast.Compare) or len(test.ops) != 1:
            return None
        op = test.ops[0]
        comp = test.comparators[0]
        # assert x is not None（排除后紧跟属性访问的合法 existence check）
        if isinstance(op, ast.IsNot) and isinstance(comp, ast.Constant) and comp.value is None:
            if isinstance(test.left, ast.Name):
                if not cls._followed_by_attribute_access(node, test.left.id, parents):
                    return f"assert {test.left.id} is not None"
        # assert len(x) >= 0
        if isinstance(op, ast.GtE) and isinstance(comp, ast.Constant):
            if type(comp.value) is int and comp.value == 0:
                if isinstance(test.left, ast.Call) and isinstance(test.left.func, ast.Name):
                    if test.left.func.id == "len":
                        return "assert len(x) >= 0"
        # assert x == x（自比较）
        if isinstance(op, ast.Eq) and isinstance(test.left, ast.Name) and isinstance(comp, ast.Name):
            if test.left.id == comp.id:
                return f"assert {test.left.id} == {test.left.id}"
        return None

    @staticmethod
    def _weak_assert_call_reason(node: Any) -> Optional[str]:
        """识别 assertTrue(True) / assertFalse(False) 恒真调用"""
        if not isinstance(node.func, ast.Name):
            return None
        if len(node.args) != 1:
            return None
        arg = node.args[0]
        if not isinstance(arg, ast.Constant):
            return None
        if node.func.id == "assertTrue" and arg.value is True:
            return "assertTrue(True)"
        if node.func.id == "assertFalse" and arg.value is False:
            return "assertFalse(False)"
        return None

    @classmethod
    def _followed_by_attribute_access(
        cls, node: Any, name: str, parents: Dict[Any, Any]
    ) -> bool:
        """
        判断 assert 语句后紧跟的语句是否对 name 做属性访问

        `assert x is not None; x.do_something()` 是合理的 existence check，
        R27 豁免；仅检查同一语句块中的下一语句。
        """
        cur = node
        while cur in parents:
            parent = parents[cur]
            for field_value in vars(parent).values():
                if not isinstance(field_value, list):
                    continue
                if not any(item is cur for item in field_value):
                    continue
                idx = field_value.index(cur)
                if idx + 1 >= len(field_value):
                    return False  # assert 是块内最后一条语句，无后续访问
                for n in ast.walk(field_value[idx + 1]):
                    if (
                        isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name)
                        and n.value.id == name
                    ):
                        return True
                return False
            cur = parent
        return False

    # ═══════════════════════════════════════════════════════════════
    # R28 无断言测试函数
    # ═══════════════════════════════════════════════════════════════

    def _check_no_assertions(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测无断言测试函数（R28）

        def test_* / async def test_* 函数体中无任何断言语句（assert /
        pytest.raises / expect / should / assert* 方法），无法验证任何行为。
        排除：conftest.py、函数名不含 test_ 前缀、名称含 setup/teardown/fixture。
        """
        findings: List[Finding] = []
        if file_path.rsplit("/", 1)[-1] == "conftest.py":
            return findings  # conftest.py 排除

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        for func in self._iter_test_functions(tree):
            lower = func.name.lower()
            if any(k in lower for k in ("setup", "teardown", "fixture")):
                continue
            if self._has_assertion(func):
                continue
            findings.append(
                self._build_finding(
                    file_path, func.lineno, evidence_id, "R28",
                    Severity.ERROR, 0.90,
                    "无断言测试函数",
                    f"测试函数 '{func.name}' 没有任何断言语句",
                    "无断言的测试永远通过，无法捕获任何回归，属于无效测试",
                    "补充真实断言（assert 具体值/状态、pytest.raises、expect）",
                )
            )
        return findings

    @classmethod
    def _has_assertion(cls, func: Any) -> bool:
        """判断函数体（不含嵌套函数）是否含断言语句或断言库调用"""
        for node in cls._iter_stmts_excluding_nested(func):
            if isinstance(node, ast.Assert):
                return True
            if isinstance(node, ast.Call) and cls._is_assertion_call(node):
                return True
        return False

    @staticmethod
    def _is_assertion_call(call: Any) -> bool:
        """
        判断调用是否为断言库调用（pytest.raises / expect / should / assert* 方法）
        """
        func = call.func
        if isinstance(func, ast.Name):
            return func.id in ("expect", "should")
        if isinstance(func, ast.Attribute):
            if func.attr == "raises":
                return isinstance(func.value, ast.Name) and func.value.id == "pytest"
            if func.attr in ("expect", "should"):
                return True
            if func.attr.startswith("assert"):
                return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # R29 魔法值断言
    # ═══════════════════════════════════════════════════════════════

    def _check_magic_value_assertions(
        self, content: str, file_path: str, evidence_id: str
    ) -> List[Finding]:
        """
        检测魔法值断言（R29）

        assert x == <literal> 中的 int/float/str 字面量若未在文件模块级定义为
        命名常量，则是"魔法值断言"——测试意图不可读且难以维护。
        排除：0/1/""/None/True/False 常见基准值。
        """
        findings: List[Finding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        constants = self._collect_module_constants(tree)

        for func in self._iter_test_functions(tree):
            for node in self._iter_stmts_excluding_nested(func):
                if not isinstance(node, ast.Assert):
                    continue
                for compare in self._iter_compares(node):
                    if self._magic_value_literal(compare, constants):
                        findings.append(
                            self._build_finding(
                                file_path, node.lineno, evidence_id, "R29",
                                Severity.WARNING, 0.80,
                                "魔法值断言",
                                "断言中使用魔法值字面量，且文件无对应命名常量",
                                "魔法值让测试意图不可读，需求变更时难以定位和更新",
                                "提取为模块级命名常量（如 MAX_AGE = 42）并复用",
                            )
                        )
                        break  # 每条 assert 最多一条 R29
        return findings

    @classmethod
    def _iter_compares(cls, node: Any) -> Iterator[Any]:
        """迭代 assert 语句内的 Compare 节点（跳过嵌套函数/lambda）"""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Compare):
                yield child
            yield from cls._iter_compares(child)

    @classmethod
    def _magic_value_literal(cls, compare: Any, constants: set) -> bool:
        """
        判断 Compare 是否含魔法值字面量

        字面量须为 int/float/str、非基准值，且文件中无同名常量定义。
        """
        for op, comp in zip(compare.ops, compare.comparators):
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                continue
            literal = None
            if cls._is_literal(comp):
                literal = comp.value
            elif cls._is_literal(compare.left):
                literal = compare.left.value
            if literal is None:
                continue
            if cls._is_baseline_literal(literal):
                continue
            if (type(literal).__name__, literal) in constants:
                continue
            return True
        return False

    @staticmethod
    def _is_literal(node: Any) -> bool:
        """判断节点是否为 int/float/str 字面量（bool 除外）"""
        if not isinstance(node, ast.Constant):
            return False
        value = node.value
        return isinstance(value, (int, float, str)) and not isinstance(value, bool)

    @classmethod
    def _is_baseline_literal(cls, value: Any) -> bool:
        """判断字面量是否为常见基准值（0/1/""/None/True/False）"""
        if value is None:
            return True
        if isinstance(value, bool):
            return True
        if isinstance(value, int) and value in (0, 1):
            return True
        if isinstance(value, float) and value in (0.0, 1.0):
            return True
        if isinstance(value, str) and value == "":
            return True
        return False

    @staticmethod
    def _collect_module_constants(tree: Any) -> set:
        """收集模块级命名常量值（CONST_NAME = <literal>）"""
        keys: set = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if not isinstance(node.value, ast.Constant):
                continue
            value = node.value.value
            if not isinstance(value, (int, float, str)) or isinstance(value, bool):
                continue
            for t in targets:
                if isinstance(t, ast.Name):
                    keys.add((type(value).__name__, value))
        return keys

    # ═══════════════════════════════════════════════════════════════
    # R30 测试间共享可变状态
    # ═══════════════════════════════════════════════════════════════

    def _check_shared_mutable_state(
        self,
        content: str,
        file_path: str,
        evidence_id: str,
        conftest_cleanup: bool,
    ) -> List[Finding]:
        """
        检测测试间共享可变状态（R30）

        模块级 list/dict/set 在 test 函数中被写操作（append/update/add/pop 等），
        且同文件与 conftest.py 均无 teardown/fixture 清理 → 测试间状态泄漏。
        """
        findings: List[Finding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings

        shared = self._collect_module_mutables(tree)
        if not shared:
            return findings

        mutated: Dict[str, int] = {}
        for func in self._iter_test_functions(tree):
            for var, line in self._iter_mutations_excluding_nested(func):
                if var in shared and var not in mutated:
                    mutated[var] = line
        if not mutated:
            return findings

        has_cleanup = (
            self._content_has_cleanup(content, list(shared.keys()))
            or conftest_cleanup
        )
        if has_cleanup:
            return findings

        for var in sorted(mutated):
            findings.append(
                self._build_finding(
                    file_path, shared[var], evidence_id, "R30",
                    Severity.WARNING, 0.85,
                    "测试间共享可变状态",
                    f"模块级可变对象 '{var}' 在 test 中被写操作，但无 teardown/fixture 清理",
                    "测试间共享状态会相互污染，测试执行顺序影响结果，导致偶发失败",
                    "为每个测试提供独立 fixture，或在 teardown 中清理共享状态",
                )
            )
        return findings

    @staticmethod
    def _collect_module_mutables(tree: Any) -> Dict[str, int]:
        """收集模块级 list/dict/set 变量，返回 {变量名: 定义行号}"""
        shared: Dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if not AssertionRulesEngine._is_mutable_literal(node.value):
                continue
            for t in targets:
                if isinstance(t, ast.Name):
                    shared[t.id] = node.lineno
        return shared

    @staticmethod
    def _is_mutable_literal(node: Any) -> bool:
        """判断值是否为 list/dict/set 字面量或 list()/dict()/set() 构造"""
        if isinstance(node, (ast.List, ast.Dict, ast.Set)):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return node.func.id in ("list", "dict", "set")
        return False

    @classmethod
    def _iter_mutations_excluding_nested(cls, func: Any) -> Iterator[tuple]:
        """
        迭代 test 函数内对共享变量的写操作，产出 (变量名, 行号)

        覆盖：.append/.update/.add/.pop 等方法调用、下标赋值 x[k]=v、+= 等。
        """
        for node in cls._iter_stmts_excluding_nested(func):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    if f.attr in _MUTATION_METHODS:
                        yield (f.value.id, node.lineno)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                        yield (t.value.id, node.lineno)
            elif isinstance(node, ast.AugAssign):
                if isinstance(node.target, ast.Name):
                    yield (node.target.id, node.lineno)

    @classmethod
    def _content_has_cleanup(
        cls, content: str, shared_vars: Optional[List[str]]
    ) -> bool:
        """
        判断文件是否含共享状态清理逻辑

        任一函数名含 teardown/cleanup，或 fixture 函数体对共享变量做
        .clear()/del → 视为具备清理能力。
        """
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            lower = node.name.lower()
            if "teardown" in lower or "cleanup" in lower:
                return True
            if cls._is_fixture_decorated(node) and cls._body_clears_shared(node, shared_vars):
                return True
        return False

    @staticmethod
    def _is_fixture_decorated(func: Any) -> bool:
        """判断函数是否被 @pytest.fixture 装饰"""
        for dec in func.decorator_list:
            if isinstance(dec, ast.Attribute) and dec.attr == "fixture":
                return True
            if isinstance(dec, ast.Name) and dec.id == "fixture":
                return True
        return False

    @classmethod
    def _body_clears_shared(
        cls, func: Any, shared_vars: Optional[List[str]]
    ) -> bool:
        """判断 fixture 函数体是否清理共享状态（.clear()/del）"""
        for node in cls._iter_stmts_excluding_nested(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "clear" and isinstance(node.func.value, ast.Name):
                    if shared_vars is None or node.func.value.id in shared_vars:
                        return True
            if isinstance(node, ast.Delete):
                for t in node.targets:
                    if isinstance(t, ast.Name) and (shared_vars is None or t.id in shared_vars):
                        return True
        return False

    @classmethod
    def _any_conftest_has_cleanup(
        cls, normalized_artifacts: List[Dict[str, Any]]
    ) -> bool:
        """预扫描所有 conftest.py artifact 是否具备清理能力"""
        for artifact in normalized_artifacts:
            path = artifact.get("path", "")
            if not path.endswith(".py"):
                continue
            if path.rsplit("/", 1)[-1] != "conftest.py":
                continue
            if cls._content_has_cleanup(artifact.get("content", ""), None):
                return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # 公共辅助
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def _iter_test_functions(cls, tree: Any) -> Iterator[Any]:
        """迭代文件中所有 test_ 前缀函数（含 async）"""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    yield node

    @classmethod
    def _iter_stmts_excluding_nested(cls, func: Any) -> Iterator[Any]:
        """迭代函数体内语句，跳过嵌套函数/lambda 子树"""
        for child in ast.iter_child_nodes(func):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from cls._iter_stmts_excluding_nested(child)

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
            dimension="test-quality",
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
