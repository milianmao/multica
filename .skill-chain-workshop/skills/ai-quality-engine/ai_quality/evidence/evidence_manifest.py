"""
backend/evidence/evidence_manifest.py

Evidence Manifest 生成器

从 Artifact 列表生成 Evidence Manifest，每个 Evidence 包含：
- evidence_id: 唯一标识
- type: Evidence 类型
- status: Evidence 状态
- source: 来源标识
- captured_at: 捕获时间
- artifact_digest: 关联的 Artifact digest
- trust_level: 信任级别
- size_bytes: Evidence 大小

对应数据流节点：G（Evidence Manifest）
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class EvidenceManifestGenerator:
    """
    Evidence Manifest 生成器

    从 Artifact 列表生成 Evidence Manifest
    """

    @staticmethod
    def generate_manifest(
        artifacts: List[Dict[str, Any]],
        artifact_digest: str,
        source: str = "skill.local_file",
        trust_level: str = "untrusted_input",
    ) -> List[Dict[str, Any]]:
        """
        从 Artifact 列表生成 Evidence Manifest

        Args:
            artifacts: Artifact 列表（已规范化）
            artifact_digest: Artifact digest
            source: 来源标识
            trust_level: 信任级别

        Returns:
            Evidence 列表
        """
        evidence_list = []

        for artifact in artifacts:
            evidence = {
                "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                "path": artifact["path"],
                "type": artifact["type"],
                "status": "present",
                "hash": None,  # 可选，可由调用方填充
                "source": source,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "artifact_digest": artifact_digest,
                "trust_level": trust_level,
                "size_bytes": len(artifact["content"].encode("utf-8")),
            }
            evidence_list.append(evidence)

        return evidence_list

    @staticmethod
    def generate_manifest_with_ids(
        artifacts: List[Dict[str, Any]],
        artifact_digest: str,
        source: str = "skill.local_file",
        trust_level: str = "untrusted_input",
    ) -> List[Dict[str, Any]]:
        """
        生成 Evidence Manifest（使用 evidence_id 作为稳定标识）

        与 generate_manifest 不同，此方法使用 path 的 hash 作为 evidence_id，
        确保相同 path 在不同评测中产生相同的 evidence_id。

        Args:
            artifacts: Artifact 列表（已规范化）
            artifact_digest: Artifact digest
            source: 来源标识
            trust_level: 信任级别

        Returns:
            Evidence 列表
        """
        import hashlib

        evidence_list = []

        for artifact in artifacts:
            # 使用 path 的 hash 作为稳定的 evidence_id
            path_hash = hashlib.sha256(artifact["path"].encode("utf-8")).hexdigest()[:12]
            evidence = {
                "evidence_id": f"ev_{path_hash}",
                "path": artifact["path"],
                "type": artifact["type"],
                "status": "present",
                "hash": None,
                "source": source,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "artifact_digest": artifact_digest,
                "trust_level": trust_level,
                "size_bytes": len(artifact["content"].encode("utf-8")),
            }
            evidence_list.append(evidence)

        return evidence_list

    @staticmethod
    def create_evidence_ref(
        evidence_id: str,
        evidence_type: str,
        artifact_digest: str,
        source: str = "skill.local_file",
        trust_level: str = "untrusted_input",
        size_bytes: Optional[int] = None,
        status: str = "present",
    ) -> Dict[str, Any]:
        """
        创建单个 Evidence 引用

        Args:
            evidence_id: Evidence ID
            evidence_type: Evidence 类型
            artifact_digest: Artifact digest
            source: 来源标识
            trust_level: 信任级别
            size_bytes: Evidence 大小
            status: Evidence 状态

        Returns:
            Evidence 字典
        """
        return {
            "evidence_id": evidence_id,
            "type": evidence_type,
            "status": status,
            "hash": None,
            "source": source,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "artifact_digest": artifact_digest,
            "trust_level": trust_level,
            "size_bytes": size_bytes,
        }

    @staticmethod
    def generate_oracle_evidence(
        oracle_manifest_dir: Optional[str],
        artifact_digest: str,
        source: str = "skill.local_file",
        trust_level: str = "untrusted_input",
    ) -> List[Dict[str, Any]]:
        """
        为 v0.7 code-verified Profile 生成 Oracle Evidence 类型。

        对 oracle_manifest_dir 下存在的 manifest 文件（不解析内容合法性，
        存在性判定交给 OracleRulesEngine）产出对应 Evidence，供 Profile Gate
        识别 code-verified 是否具备 contract_oracle / scenario_oracle 证据。

        Fail-closed：manifest 文件不存在 → 不产出对应 evidence 类型 → Profile Gate
        判定 WITHHELD（不是补一个假的 evidence）。

        Args:
            oracle_manifest_dir: 存放 oracle-manifest.json / scenario-manifest.json 的目录
            artifact_digest: artifact digest
            source: 来源标识
            trust_level: 信任级别

        Returns:
            Oracle Evidence 列表（type=contract_oracle / scenario_oracle）
        """
        if not oracle_manifest_dir:
            return []

        import hashlib
        from pathlib import Path

        root = Path(oracle_manifest_dir)
        evidence_list: List[Dict[str, Any]] = []

        # contract_oracle 证据：oracle-manifest.json 存在即视为 present
        contract_path = root / "oracle-manifest.json"
        if contract_path.is_file():
            ev_id = f"ev_{hashlib.sha256(str(contract_path).encode('utf-8')).hexdigest()[:12]}"
            evidence_list.append({
                "evidence_id": ev_id,
                "path": str(contract_path),
                "type": "contract_oracle",
                "status": "present",
                "hash": None,
                "source": source,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "artifact_digest": artifact_digest,
                "trust_level": trust_level,
                "size_bytes": contract_path.stat().st_size if contract_path.is_file() else 0,
            })

        # scenario_oracle 证据：scenario-manifest.json 存在即视为 present
        scenario_path = root / "scenario-manifest.json"
        if scenario_path.is_file():
            ev_id = f"ev_{hashlib.sha256(str(scenario_path).encode('utf-8')).hexdigest()[:12]}"
            evidence_list.append({
                "evidence_id": ev_id,
                "path": str(scenario_path),
                "type": "scenario_oracle",
                "status": "present",
                "hash": None,
                "source": source,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "artifact_digest": artifact_digest,
                "trust_level": trust_level,
                "size_bytes": scenario_path.stat().st_size if scenario_path.is_file() else 0,
            })

        return evidence_list
