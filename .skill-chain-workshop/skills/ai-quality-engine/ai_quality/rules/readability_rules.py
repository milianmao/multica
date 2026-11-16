"""
backend/rules/readability_rules.py

可读性规则引擎 - 实现 readability 维度的静态检查规则

对应数据流节点：I（Static Rules - Readability）

实现的规则：
- R10: 魔法数字检测
- R11: 变量命名不规范检测
- R12: 函数命名不规范检测
"""

from __future__ import annotations

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


class ReadabilityRulesEngine:
    """
    可读性规则引擎

    执行 readability 维度的静态检查规则，提升代码可读性。
    """

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行所有可读性规则

        Args:
            normalized_artifacts: 规范化后的 Artifact 列表
            evidence_manifest: Evidence Manifest

        Returns:
            Finding 列表
        """
        findings: List[Finding] = []

        for artifact in normalized_artifacts:
            evidence_id = self._find_evidence_id(artifact, evidence_manifest)

            # 规则 10: 魔法数字检测
            findings.extend(self._check_magic_numbers(artifact, evidence_id))

            # 规则 11: 变量命名不规范检测
            findings.extend(self._check_variable_naming(artifact, evidence_id))

            # 规则 12: 函数命名不规范检测
            findings.extend(self._check_function_naming(artifact, evidence_id))

        return findings

    def _find_evidence_id(
        self, artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
    ) -> str:
        """根据 artifact path 精确匹配对应的 evidence_id"""
        return match_evidence_id(artifact, evidence_manifest)

    def _check_magic_numbers(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测魔法数字（R10）

        检测模式：
        - 未命名的数字常量（排除 0, 1, -1, 100, 1000）
        """
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        # 排除的常见数字
        excluded_numbers = {0, 1, -1, 100, 1000}

        for line_num, line in enumerate(lines, 1):
            # 跳过注释行
            if line.strip().startswith("//") or line.strip().startswith("#"):
                continue

            # 检测数字（排除字符串内的）
            number_pattern = r'(?<!["\'])\b(\d+\.?\d*)\b(?!["\'])'
            matches = re.finditer(number_pattern, line)

            for match in matches:
                number = float(match.group())

                # 排除常见数字
                if number in excluded_numbers:
                    continue

                # 排除版本号、端口号等
                if self._is_contextual_number(line, match.start()):
                    continue

                findings.append(
                    Finding(
                        finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                        dimension="readability",
                        rule_id="R10",
                        severity=Severity.WARNING,
                        confidence=Confidence(
                            value=0.5,
                            kind=ConfidenceKind.HEURISTIC,
                        ),
                        title="魔法数字",
                        symptom=f"使用未命名的数字常量 {match.group()}",
                        source=FindingSource(
                            evidence_id=evidence_id,
                            file=artifact["path"],
                            line_start=line_num,
                            line_end=line_num,
                        ),
                        consequence="代码含义不明确，难以理解数字的业务含义",
                        remedy="将数字提取为命名常量（如 MAX_RETRY_COUNT = 3）",
                    )
                )

        return findings

    def _is_contextual_number(self, line: str, pos: int) -> bool:
        """判断数字是否是上下文相关的（如版本号、端口号）"""
        # 简单启发式：检查前后文
        before = line[:pos].strip()
        after = line[pos:].strip()

        # 版本号（如 1.0.0）
        if "." in after and after[0].isdigit():
            return True

        # 端口号（如 8080, 3000）
        if before.endswith("port") or before.endswith(":"):
            return True

        return False

    def _check_variable_naming(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测变量命名不规范（R11）

        检测模式：
        - 单字母变量（循环变量 i, j, k 除外）
        - 拼音命名
        """
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        # 允许的单字母变量
        allowed_single_letters = {"i", "j", "k", "x", "y", "z", "e", "n", "m"}

        # 语言门控：JS/TS 只认声明语句（var/let/const），Python 只认赋值（排除 == 比较）
        is_python = artifact.get("path", "").endswith(".py")
        var_patterns = (
            [r"(\w+)\s*=\s*(?!=)"]  # Python: x = v，排除 x == v 比较
            if is_python
            else [r"(?:var|let|const)\s+([a-zA-Z_]\w*)"]  # JS/TS: const x = v
        )

        for line_num, line in enumerate(lines, 1):
            # 跳过注释行
            if line.strip().startswith("//") or line.strip().startswith("#"):
                continue

            for pattern in var_patterns:
                matches = re.finditer(pattern, line)
                for match in matches:
                    var_name = match.group(1)

                    # 跳过允许的单字母变量
                    if var_name in allowed_single_letters:
                        continue

                    # 检测单字母变量（非允许列表）
                    if len(var_name) == 1 and var_name.isalpha():
                        findings.append(
                            Finding(
                                finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                                dimension="readability",
                                rule_id="R11",
                                severity=Severity.INFO,
                                confidence=Confidence(
                                    value=0.6,
                                    kind=ConfidenceKind.HEURISTIC,
                                ),
                                title="变量命名不规范",
                                symptom=f"变量名 '{var_name}' 过于简短，含义不明确",
                                source=FindingSource(
                                    evidence_id=evidence_id,
                                    file=artifact["path"],
                                    line_start=line_num,
                                    line_end=line_num,
                                ),
                                consequence="代码可读性差，难以理解变量用途",
                                remedy="使用有意义的变量名（如 userCount 而非 n）",
                            )
                        )

        return findings

    def _check_function_naming(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测函数命名不规范（R12）

        检测模式：
        - 拼音命名
        - 无意义缩写
        """
        findings: List[Finding] = []
        content = artifact["content"]
        lines = content.split("\n")

        # 拼音模式（简化检测）
        pinyin_patterns = [
            r'(?:function|def)\s+(chuli|jisuan|huoqu|shezhi)\b',
            r'(?:function|def)\s+([a-z]{1,2})\b',  # 超短函数名
        ]

        for line_num, line in enumerate(lines, 1):
            for pattern in pinyin_patterns:
                matches = re.finditer(pattern, line, re.IGNORECASE)
                for match in matches:
                    func_name = match.group(1)

                    findings.append(
                        Finding(
                            finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                            dimension="readability",
                            rule_id="R12",
                            severity=Severity.INFO,
                            confidence=Confidence(
                                value=0.6,
                                kind=ConfidenceKind.HEURISTIC,
                            ),
                            title="函数命名不规范",
                            symptom=f"函数名 '{func_name}' 含义不明确或为拼音",
                            source=FindingSource(
                                evidence_id=evidence_id,
                                file=artifact["path"],
                                line_start=line_num,
                                line_end=line_num,
                            ),
                            consequence="代码可读性差，难以理解函数功能",
                            remedy="使用英文动词开头的函数名（如 calculateTotal, getUserInfo）",
                        )
                    )

        return findings
