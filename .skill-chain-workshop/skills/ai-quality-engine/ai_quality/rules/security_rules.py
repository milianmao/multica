"""
backend/rules/security_rules.py

安全性规则引擎 - 实现 security 维度的静态检查规则

对应数据流节点：I（Static Rules - Security）

实现的规则：
- R04: 硬编码密钥检测
- R05: SQL注入风险检测
- R06: XSS漏洞检测
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


class SecurityRulesEngine:
    """
    安全性规则引擎

    执行 security 维度的静态检查规则，检测常见安全漏洞。
    """

    # 硬编码密钥模式
    SECRET_PATTERNS = [
        r'(?:password|passwd|pwd)\s*[:=]\s*["\'][^"\']+["\']',
        r'(?:secret|api_key|apikey|token|access_token)\s*[:=]\s*["\'][^"\']+["\']',
        r'(?:private_key|privatekey)\s*[:=]\s*["\'][^"\']+["\']',
    ]

    # SQL注入模式（字符串拼接）
    SQL_INJECTION_PATTERNS = [
        r'(?:SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER)\s+.*["\'].*\+',
        r'(?:query|sql|execute)\s*[:=]\s*["\'].*\+',
        r'(?:query|sql|execute)\s*\(\s*["\'].*\$',
    ]

    # XSS漏洞模式
    XSS_PATTERNS = [
        r'innerHTML\s*=\s*.*(?:response|data|input|user)',
        r'document\.write\s*\(.*(?:response|data|input|user)',
        r'\.html\s*\(.*(?:response|data|input|user)',
    ]

    def execute(
        self,
        normalized_artifacts: List[Dict[str, Any]],
        evidence_manifest: List[Dict[str, Any]],
    ) -> List[Finding]:
        """
        执行所有安全性规则

        Args:
            normalized_artifacts: 规范化后的 Artifact 列表
            evidence_manifest: Evidence Manifest

        Returns:
            Finding 列表
        """
        findings: List[Finding] = []

        for artifact in normalized_artifacts:
            evidence_id = self._find_evidence_id(artifact, evidence_manifest)

            # 规则 4: 硬编码密钥检测
            findings.extend(self._check_hardcoded_secrets(artifact, evidence_id))

            # 规则 5: SQL注入风险检测
            findings.extend(self._check_sql_injection(artifact, evidence_id))

            # 规则 6: XSS漏洞检测
            findings.extend(self._check_xss_vulnerability(artifact, evidence_id))

        return findings

    def _find_evidence_id(
        self, artifact: Dict[str, Any], evidence_manifest: List[Dict[str, Any]]
    ) -> str:
        """根据 artifact path 精确匹配对应的 evidence_id"""
        return match_evidence_id(artifact, evidence_manifest)

    def _check_hardcoded_secrets(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测硬编码密钥（R04）

        检测模式：
        - password = "xxx"
        - api_key = "xxx"
        - token = "xxx"
        """
        findings: List[Finding] = []
        content = artifact["content"]

        for pattern in self.SECRET_PATTERNS:
            matches = re.finditer(pattern, content, re.MULTILINE | re.IGNORECASE)
            for match in matches:
                line_num = content[: match.start()].count("\n") + 1
                matched_text = match.group()

                # 排除环境变量引用
                if "process.env" in matched_text or "os.environ" in matched_text or "getenv" in matched_text:
                    continue

                findings.append(
                    Finding(
                        finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                        dimension="security",
                        rule_id="R04",
                        severity=Severity.CRITICAL,
                        confidence=Confidence(
                            value=0.85,
                            kind=ConfidenceKind.HEURISTIC,
                        ),
                        title="硬编码密钥",
                        symptom="在代码中硬编码敏感信息",
                        source=FindingSource(
                            evidence_id=evidence_id,
                            file=artifact["path"],
                            line_start=line_num,
                            line_end=line_num,
                        ),
                        consequence="密钥泄露风险，攻击者可直接获取访问权限",
                        remedy="使用环境变量或密钥管理服务（如 AWS Secrets Manager、HashiCorp Vault）",
                    )
                )

        return findings

    def _check_sql_injection(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测SQL注入风险（R05）

        检测模式：
        - 字符串拼接构建 SQL 查询
        - "SELECT * FROM users WHERE id=" + userId
        """
        findings: List[Finding] = []
        content = artifact["content"]

        for pattern in self.SQL_INJECTION_PATTERNS:
            matches = re.finditer(pattern, content, re.MULTILINE | re.IGNORECASE)
            for match in matches:
                line_num = content[: match.start()].count("\n") + 1

                findings.append(
                    Finding(
                        finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                        dimension="security",
                        rule_id="R05",
                        severity=Severity.ERROR,
                        confidence=Confidence(
                            value=0.9,
                            kind=ConfidenceKind.HEURISTIC,
                        ),
                        title="SQL注入风险",
                        symptom="使用字符串拼接构建 SQL 查询",
                        source=FindingSource(
                            evidence_id=evidence_id,
                            file=artifact["path"],
                            line_start=line_num,
                            line_end=line_num,
                        ),
                        consequence="攻击者可通过构造恶意输入执行任意 SQL 命令，导致数据泄露或破坏",
                        remedy="使用参数化查询或 ORM 框架（如 SQLAlchemy parameterized queries）",
                    )
                )

        return findings

    def _check_xss_vulnerability(
        self, artifact: Dict[str, Any], evidence_id: str
    ) -> List[Finding]:
        """
        检测XSS漏洞（R06）

        检测模式：
        - innerHTML = userInput
        - document.write(userInput)
        """
        findings: List[Finding] = []
        content = artifact["content"]

        for pattern in self.XSS_PATTERNS:
            matches = re.finditer(pattern, content, re.MULTILINE | re.IGNORECASE)
            for match in matches:
                line_num = content[: match.start()].count("\n") + 1

                findings.append(
                    Finding(
                        finding_id=f"finding_{uuid.uuid4().hex[:12]}",
                        dimension="security",
                        rule_id="R06",
                        severity=Severity.ERROR,
                        confidence=Confidence(
                            value=0.88,
                            kind=ConfidenceKind.HEURISTIC,
                        ),
                        title="XSS漏洞",
                        symptom="直接将用户输入插入 HTML 页面",
                        source=FindingSource(
                            evidence_id=evidence_id,
                            file=artifact["path"],
                            line_start=line_num,
                            line_end=line_num,
                        ),
                        consequence="攻击者可注入恶意脚本，窃取用户会话、重定向到恶意网站",
                        remedy="使用安全的 DOM 操作方法（如 textContent），或对输入进行 HTML 转义",
                    )
                )

        return findings
