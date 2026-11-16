"""
backend/evidence/artifact_normalizer.py

Artifact 规范化器 - 确保相同输入产生相同 digest

规范化规则：
1. 按路径排序（稳定排序）
2. 编码规范化（UTF-8）
3. 换行规范化（LF）
4. SHA-256 digest 计算

对应数据流节点：F（Artifact Normalization）
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List


class ArtifactNormalizer:
    """
    Artifact 规范化器

    确保相同输入产生相同的 SHA-256 digest：
    - 路径排序：按字典序排序
    - 编码规范化：统一使用 UTF-8
    - 换行规范化：CRLF 和 CR 统一为 LF
    - 内容变化产生新 digest
    """

    @staticmethod
    def normalize_artifacts(artifacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        规范化 Artifact 列表

        Args:
            artifacts: Artifact 列表，每个 artifact 包含 type、path、content、encoding

        Returns:
            规范化后的 Artifact 列表（按路径排序）
        """
        normalized = []
        for artifact in artifacts:
            # 换行规范化：CRLF -> LF, CR -> LF
            content = artifact["content"].replace("\r\n", "\n").replace("\r", "\n")
            normalized.append(
                {
                    "type": artifact["type"],
                    "path": artifact["path"].strip(),
                    "content": content,
                    "encoding": "utf-8",
                }
            )

        # 按路径排序（稳定排序，确保相同输入产生相同 digest）
        normalized.sort(key=lambda x: x["path"])
        return normalized

    @staticmethod
    def compute_digest(artifacts: List[Dict[str, Any]]) -> str:
        """
        计算规范化 Artifact 的 SHA-256 digest

        Args:
            artifacts: Artifact 列表

        Returns:
            SHA-256 digest 字符串（格式：sha256:...）
        """
        normalized = ArtifactNormalizer.normalize_artifacts(artifacts)

        # 构建哈希输入（路径 + 内容），使用分隔符避免歧义
        hash_input = ""
        for artifact in normalized:
            # 使用 \x1f (Unit Separator) 作为字段分隔符，避免内容冲突
            hash_input += f"{artifact['path']}\x1f{artifact['content']}\x1e"

        # SHA-256 计算
        digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    @staticmethod
    def normalize_and_digest(
        artifacts: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], str]:
        """
        规范化 Artifact 并计算 digest

        Args:
            artifacts: Artifact 列表

        Returns:
            (规范化后的 Artifact 列表, SHA-256 digest)
        """
        normalized = ArtifactNormalizer.normalize_artifacts(artifacts)
        digest = ArtifactNormalizer.compute_digest(artifacts)
        return normalized, digest
