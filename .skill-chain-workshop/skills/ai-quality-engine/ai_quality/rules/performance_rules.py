"""
backend/rules/performance_rules.py

性能规则引擎 - 实现 performance 维度的静态检查规则

对应数据流节点：I（Static Rules - Performance）

实现的规则：
- R13: 循环内IO检测
- R14: N+1查询检测
- R15: 内存泄漏风险检测

判定策略（多语言门控）：
- Python：AST 解析，精确识别循环体内 IO/DB 调用，避免嵌套循环重复报告
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


class PerformanceRulesEngine:
    """
    性能规则引擎

    执行 performance 维度的静态检查规则，检测常见性能问题。
    """

    # IO 操作调用名标记（Python + JS，子串/前后缀匹配）
    IO_EXACT = {"open", "fetch", "requests", "axios", "httpx", "aiohttp"}
    IO_PREFIX = (
        "fs.",
        "requests.",
        "urllib.",
        "httpx.",
        "aiohttp.",
        "axios.",
        "db.",
        "session.",
        "cursor.",
        "http.",
    )
    IO_SUFFIX = (
        ".read",
        ".write",
        ".readFileSync",
        ".writeFileSync",
        ".execute",
        ".query",
        ".fetch",
        ".request",
        ".post",
        ".get",
        ".put",
        ".delete",
    )

    # 数据库查询调用名标记（N+1 检测专用）
    DB_EXACT = {"query", "find", "findOne", "execute", "findAll", "findFirst"}
    DB_PREFIX = ("db.", "session.", "cursor.", "repository.")
    DB_SUFFIX = (
        ".query",
        ".find",
        ".find_one",
        ".findFirst",
        ".findMany",
        ".findAll",
        ".execute",
        ".fetch_all",
        ".all",
        ".filter",
        ".create",
        ".save",
        ".commit",
    )

    # 内存泄漏模式（保留启发式，词频统计）
    MEMORY_LEAK_PATTERNS = [
        r'\baddEventListener\b(?!.*removeEventListener)',
        r'\bsetInterval\b(?!.*clearInterval)',
        r'\bsetTimeout\b(?!.*clearTimeout)',
    ]

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行所有性能规则

        Args:
            normalized_artifacts: 规范化后的 Artifact 列表
            evidence_manifest: Evidence Manifest

        Returns:
            Finding 列表
        """
        findings: List[Finding] = []

        for artifact in normalized_artifacts:
            evidence_id = self._find_evidence_id(artifact, evidence_manifest)

            # 规则 13: 循环内IO检测
            findings.extend(self._check_io_in_loop(artifact, evidence_id))

            # 规则 14: N+1查询检测
            findings.extend(self._check_n_plus_one(artifact, evidence_id))

            # 规则 15: 内存泄漏风险检测
            findings.extend(self._check_memory_leak(artifact, evidence_id))

        return findings

    def _find_evidence_id(
        self, artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
    ) -> str:
        """根据 artifact path 精确匹配对应的 evidence_id"""
        return match_evidence_id(artifact, evidence_manifest)

    # ═══════════════════════════════════════════════════════════════
    # R13 循环内 IO
    # ═══════════════════════════════════════════════════════════════

    def _check_io_in_loop(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测循环内IO操作（R13）

        Python 用 AST 精确识别循环体内的 IO 调用；
        JS/TS 用花括号配对（无 AST 依赖）。
        """
        if artifact.get("path", "").endswith(".py"):
            return self._check_io_in_loop_python(artifact, evidence_id)
        return self._check_io_in_loop_brace(artifact, evidence_id)

    def _check_io_in_loop_python(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """Python 循环内 IO：AST 遍历，每个 IO 调用报一次（找最近循环祖先）。"""
        findings: List[Finding] = []
        try:
            tree = ast.parse(artifact["content"])
        except SyntaxError:
            return findings

        parents = self._build_parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not self._is_io_call(self._call_name(node)):
                continue
            if not self._in_loop(node, parents):
                continue
            findings.append(
                self._build_loop_finding(
                    artifact["path"], node.lineno, evidence_id, "R13",
                    "循环内IO操作", Severity.ERROR, 0.85,
                    "在循环内执行IO操作（文件/网络/数据库）",
                    "每次循环都执行IO操作，导致性能严重下降",
                    "批量处理IO操作，或使用缓存减少IO次数",
                )
            )
        return findings

    def _check_io_in_loop_brace(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """JS/TS 循环内 IO：循环头正则 + IO 模式 + 花括号配对（保留启发式）。"""
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        in_loop = False
        loop_depth = 0

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()

            # 检测循环开始
            if re.search(r'\b(for|while|do)\s*[({]', stripped):
                in_loop = True
                loop_depth += 1

            if in_loop:
                # 检测IO操作
                if re.search(
                    r'\b(?:readFileSync|writeFileSync|fs\.|fetch|axios|request|http\.|query|execute|db\.)\b',
                    stripped,
                    re.IGNORECASE,
                ):
                    findings.append(
                        self._build_loop_finding(
                            artifact["path"], line_num, evidence_id, "R13",
                            "循环内IO操作", Severity.ERROR, 0.85,
                            "在循环内执行IO操作（文件/网络/数据库）",
                            "每次循环都执行IO操作，导致性能严重下降",
                            "批量处理IO操作，或使用缓存减少IO次数",
                        )
                    )

                # 检测循环结束
                if "}" in stripped:
                    loop_depth -= stripped.count("}")
                    if loop_depth <= 0:
                        in_loop = False

        return findings

    # ═══════════════════════════════════════════════════════════════
    # R14 N+1 查询
    # ═══════════════════════════════════════════════════════════════

    def _check_n_plus_one(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测N+1查询问题（R14）

        Python 用 AST 识别循环体内的数据库调用；JS/TS 保留花括号配对。
        """
        if artifact.get("path", "").endswith(".py"):
            return self._check_n_plus_one_python(artifact, evidence_id)
        return self._check_n_plus_one_brace(artifact, evidence_id)

    def _check_n_plus_one_python(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """Python N+1：AST 遍历，循环内每个 DB 调用报一次。"""
        findings: List[Finding] = []
        try:
            tree = ast.parse(artifact["content"])
        except SyntaxError:
            return findings

        parents = self._build_parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not self._is_db_call(self._call_name(node)):
                continue
            if not self._in_loop(node, parents):
                continue
            findings.append(
                self._build_loop_finding(
                    artifact["path"], node.lineno, evidence_id, "R14",
                    "N+1查询问题", Severity.WARNING, 0.85,
                    "在循环内执行数据库查询",
                    "循环N次执行N次数据库查询，性能低下",
                    "使用 JOIN 或 IN 查询一次性获取所有数据",
                )
            )
        return findings

    def _check_n_plus_one_brace(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """JS/TS N+1：循环（含 forEach/map/filter）内 DB 调用（保留启发式）。"""
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        in_loop = False
        loop_depth = 0

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()

            if re.search(r'\b(for|while|do|forEach|map|filter)\s*[({]', stripped):
                in_loop = True
                loop_depth += 1

            if in_loop:
                if re.search(
                    r'\b(?:SELECT|INSERT|UPDATE|DELETE|query|execute|find|findOne|db\.|\.query|\.execute)\b',
                    stripped,
                    re.IGNORECASE,
                ):
                    findings.append(
                        self._build_loop_finding(
                            artifact["path"], line_num, evidence_id, "R14",
                            "N+1查询问题", Severity.WARNING, 0.85,
                            "在循环内执行数据库查询",
                            "循环N次执行N次数据库查询，性能低下",
                            "使用 JOIN 或 IN 查询一次性获取所有数据",
                        )
                    )

                if "}" in stripped:
                    loop_depth -= stripped.count("}")
                    if loop_depth <= 0:
                        in_loop = False

        return findings

    # ═══════════════════════════════════════════════════════════════
    # R15 内存泄漏（保留启发式词频统计）
    # ═══════════════════════════════════════════════════════════════

    def _check_memory_leak(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测内存泄漏风险（R15）

        改进为变量级配对分析（仅 JS/TS 生效，Python 无对应语义）：
        - addEventListener/removeEventListener 按 (目标元素, 事件类型) 配对；
          React useEffect 中 add + cleanup remove 同 key 则不算泄漏。
        - setInterval 返回值变量赋值后，检查是否有对应 clearInterval。
        """
        path = artifact.get("path", "")
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            return []

        content = artifact["content"]
        findings: List[Finding] = []

        # 1. 事件监听器配对：(目标元素, 事件类型) -> 行号列表
        add_re = re.compile(
            r"([\w.$\[\]]+)\s*\.\s*addEventListener\s*\(\s*[\"']([^\"']+)[\"']"
        )
        rem_re = re.compile(
            r"([\w.$\[\]]+)\s*\.\s*removeEventListener\s*\(\s*[\"']([^\"']+)[\"']"
        )
        adds: Dict[Tuple[str, str], List[int]] = {}
        removes: set = set()

        for m in add_re.finditer(content):
            key = (m.group(1).strip(), m.group(2))
            adds.setdefault(key, []).append(content[: m.start()].count("\n") + 1)
        for m in rem_re.finditer(content):
            removes.add((m.group(1).strip(), m.group(2)))

        for (target, etype), lines in adds.items():
            if (target, etype) in removes:
                continue
            for line in lines:
                findings.append(
                    self._build_listener_finding(
                        target, etype, line, artifact["path"], evidence_id
                    )
                )

        # 2. 定时器：setInterval 返回值变量未 clearInterval
        interval_re = re.compile(
            r"(?:const|let|var)\s+(\w+)\s*=\s*setInterval\s*\("
        )
        cleared_re = re.compile(r"clearInterval\s*\(\s*(\w+)\s*\)")
        interval_vars = set(interval_re.findall(content))
        cleared_vars = set(cleared_re.findall(content))

        for var in sorted(interval_vars - cleared_vars):
            match = re.search(
                r"(?:const|let|var)\s+" + re.escape(var) + r"\s*=\s*setInterval\s*\(",
                content,
            )
            line = content[: match.start()].count("\n") + 1 if match else 1
            findings.append(
                self._build_interval_finding(
                    var, line, artifact["path"], evidence_id
                )
            )

        return findings

    def _build_listener_finding(
        self, target: str, etype: str, line: int, file_path: str, evidence_id: str
    ) -> Finding:
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="performance",
            rule_id="R15",
            severity=Severity.WARNING,
            confidence=Confidence(
                value=0.75,
                kind=ConfidenceKind.HEURISTIC,
            ),
            title="事件监听器未移除",
            symptom=f"对 {target} 添加了 '{etype}' 事件监听器，未找到对应的 removeEventListener",
            source=FindingSource(
                evidence_id=evidence_id,
                file=file_path,
                line_start=line,
                line_end=line,
            ),
            consequence="事件监听器未移除导致内存泄漏，长时间运行后内存持续增长",
            remedy="在组件卸载或不再需要时调用 removeEventListener，或在 useEffect 清理函数中移除",
        )

    def _build_interval_finding(
        self, var: str, line: int, file_path: str, evidence_id: str
    ) -> Finding:
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="performance",
            rule_id="R15",
            severity=Severity.WARNING,
            confidence=Confidence(
                value=0.75,
                kind=ConfidenceKind.HEURISTIC,
            ),
            title="定时器未清理",
            symptom=f"setInterval 返回值赋给变量 '{var}'，未找到对应的 clearInterval",
            source=FindingSource(
                evidence_id=evidence_id,
                file=file_path,
                line_start=line,
                line_end=line,
            ),
            consequence="定时器未清理导致内存泄漏和不必要的CPU消耗",
            remedy="在组件卸载或不再需要时调用 clearInterval，或在 useEffect 清理函数中清理",
        )

    # ═══════════════════════════════════════════════════════════════
    # 共享 AST 工具
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _build_parent_map(tree: Any) -> Dict[Any, Any]:
        """构建 AST 子节点 → 父节点的映射。"""
        parents: Dict[Any, Any] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        return parents

    @staticmethod
    def _in_loop(node: Any, parents: Dict[Any, Any]) -> bool:
        """判断节点是否位于 for/while 循环体内（沿父链向上）。"""
        cur = node
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.For, ast.AsyncFor, ast.While)):
                return True
        return False

    @classmethod
    def _call_name(cls, node: ast.Call) -> str:
        """从 ast.Call 提取可读调用名，如 requests.get / open。"""
        parts = []
        cur = node.func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    @classmethod
    def _is_io_call(cls, name: str) -> bool:
        """判断调用名是否为 IO/网络/数据库操作。"""
        if name in cls.IO_EXACT:
            return True
        return any(name.startswith(p) for p in cls.IO_PREFIX) or any(
            name.endswith(s) for s in cls.IO_SUFFIX
        )

    @classmethod
    def _is_db_call(cls, name: str) -> bool:
        """判断调用名是否为数据库查询。"""
        if name in cls.DB_EXACT:
            return True
        return any(name.startswith(p) for p in cls.DB_PREFIX) or any(
            name.endswith(s) for s in cls.DB_SUFFIX
        )

    def _build_loop_finding(
        self,
        file_path: str,
        line_num: int,
        evidence_id: str,
        rule_id: str,
        title: str,
        severity: Severity,
        confidence: float,
        symptom: str,
        consequence: str,
        remedy: str,
    ) -> Finding:
        return Finding(
            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
            dimension="performance",
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
                line_start=line_num,
                line_end=line_num,
            ),
            consequence=consequence,
            remedy=remedy,
        )
