#!/usr/bin/env python3
"""统一编排 KB/SDD Oracle、测试证据与 ai-quality 严格判定。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


GATE_SCHEMA_VERSION = "business-quality-gate-v1"
GATE_BLOCK_EXIT_CODE = 3
SKILL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_ROOT.parents[1]
CONTRACT_EXTRACTOR = REPO_ROOT / "skills" / "contract-oracle-extractor" / "scripts" / "extract.py"
SCENARIO_EXTRACTOR = REPO_ROOT / "skills" / "scenario-oracle-builder" / "scripts" / "extract.py"
QUALITY_CHECK = SKILL_ROOT / "scripts" / "ai-quality-check.py"
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
VERIFY_EVIDENCE_SCHEMA_VERSION = "business-verification-v1"
VERIFY_ASSERTION_TYPES = {
    "return-value",
    "error-code",
    "state-transition",
    "side-effect",
}
SHELL_CONTROL_TOKENS = {";", "&&", "||", "|", "<", ">", ">>", "2>", "2>>"}
SDD_H1_VERSION_RE = re.compile(r"^#\s+v(\d+\.\d+\.\d+)\s+契约规范")
BUSINESS_EXT = {".py", ".ts", ".tsx", ".js", ".jsx"}


def _resolve(path: str, project_root: Path) -> Path:
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _business_flow_files(kb: Path) -> List[Path]:
    return [
        path for path in sorted(kb.glob("*.md"))
        if path.is_file() and path.name.lower() != "readme.md"
    ]


def _validate_sdd_ownership(sdd: Path) -> None:
    """机械确认 --sdd 是「当前唯一契约」：H1 版本号与父目录名 docs/vX.Y.Z/ 一致。"""
    lines = sdd.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("--sdd must be a non-empty file")
    match = SDD_H1_VERSION_RE.match(lines[0])
    if not match:
        raise ValueError("--sdd first line must be `# vX.Y.Z 契约规范 ...`")
    version = match.group(1)
    if sdd.parent.name != f"v{version}":
        raise ValueError(
            f"--sdd version directory mismatch: H1 says v{version}, parent dir is {sdd.parent.name}"
        )


def _validate_changed_files(
    targets: Sequence[Path],
    changed_files: Sequence[str],
    project_root: Path,
) -> None:
    """校验本轮改动中的业务入口文件都在 --target 中（防漏检）。"""
    if not changed_files:
        return
    target_rel = {str(target.relative_to(project_root)) for target in targets}
    target_names = {target.name for target in targets}
    for changed in changed_files:
        changed_path = Path(changed)
        if changed_path.suffix.lower() not in BUSINESS_EXT:
            continue
        if "tests" in changed_path.parts or ".test." in changed_path.name or ".spec." in changed_path.name:
            continue
        rel = changed_path.as_posix()
        if rel in target_rel or changed_path.name in target_names:
            continue
        raise ValueError(f"changed business file not in --target: {rel}")


def _sha256_paths(paths: Sequence[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        try:
            name = path.relative_to(root).as_posix()
        except ValueError:
            name = path.as_posix()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _command_record(
    command: Sequence[str],
    completed: subprocess.CompletedProcess[str],
    *,
    redact_command: bool = False,
) -> Dict[str, Any]:
    serialized = "\0".join(command).encode("utf-8")
    return {
        "command": ["<redacted-verify-command>"] if redact_command else list(command),
        "command_sha256": _sha256_bytes(serialized),
        "exit_code": completed.returncode,
        "stdout_sha256": _sha256_bytes(completed.stdout.encode("utf-8")),
        "stderr_sha256": _sha256_bytes(completed.stderr.encode("utf-8")),
    }


def _run_command(
    command: Sequence[str],
    cwd: Path,
    *,
    redact_command: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[subprocess.CompletedProcess[str], Dict[str, Any]]:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return completed, _command_record(command, completed, redact_command=redact_command)


def _pytest_summary(output: str, returncode: int) -> Dict[str, int]:
    values: Dict[str, int] = {"passed": 0, "failed": 0, "skipped": 0}
    for key in values:
        match = re.search(r"(\d+)\s+" + key, output)
        if match:
            values[key] = int(match.group(1))
    if returncode != 0 and values["failed"] == 0:
        values["failed"] = 1
    return values


def _run_pytest(paths: Sequence[Path], project_root: Path, run_id: str) -> Dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "-q"] + [str(path) for path in paths]
    completed, record = _run_command(command, project_root)
    return {
        **record,
        "schema_version": "pytest-results-v1",
        "run_id": run_id,
        "raw_exit_code": completed.returncode,
        "exit_code": 0 if completed.returncode == 0 else 1,
        "summary": _pytest_summary(completed.stdout + "\n" + completed.stderr, completed.returncode),
        "collected_tests": [str(path) for path in paths],
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _enrich_manifest(
    path: Path,
    source_sdd_hash: str,
    source_kb_hash: str,
    target_code_hash: str,
    evidence_level: str,
    run_id: str,
) -> Dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("generated manifest must be a JSON object")
    manifest["generator_version"] = manifest.get("engine_version")
    manifest["source_sdd_hash"] = source_sdd_hash
    manifest["source_kb_hash"] = source_kb_hash
    manifest["target_code_hash"] = target_code_hash
    manifest["evidence_level"] = evidence_level
    manifest["run_id"] = run_id
    _write_json(path, manifest)
    return manifest


def _load_verify_evidence(
    path: Path,
    inputs: Dict[str, str],
    run_id: str,
) -> Dict[str, Any]:
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("business-verification.json must be readable JSON") from exc
    if not isinstance(evidence, dict):
        raise ValueError("business-verification.json must be a JSON object")
    if evidence.get("schema_version") != VERIFY_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("business-verification.json has unsupported schema_version")
    if evidence.get("run_id") != run_id:
        raise ValueError("business-verification.json run_id must equal --run-id")
    for name, expected in inputs.items():
        if evidence.get(name) != expected:
            raise ValueError("business-verification.json input hashes do not match this run")
    assertions = evidence.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        raise ValueError("business-verification.json requires non-empty assertions")
    if not any(
        isinstance(assertion, dict)
        and assertion.get("type") in VERIFY_ASSERTION_TYPES
        and assertion.get("passed") is True
        for assertion in assertions
    ):
        raise ValueError("business-verification.json needs a passed business assertion")
    return evidence


def _blocking_findings(payload: Dict[str, Any]) -> List[str]:
    gate = payload.get("gate")
    if isinstance(gate, dict):
        findings = gate.get("blocking_findings")
        if isinstance(findings, list):
            return [str(item) for item in findings]
    return []


def _gate_result(
    run_dir: Path,
    run_id: str,
    evidence_level: str,
    inputs: Dict[str, str],
    steps: Dict[str, Any],
    reasons: List[str],
    engine_payload: Optional[Dict[str, Any]] = None,
    *,
    sdd_mode: str = "declared",
) -> int:
    if engine_payload is not None:
        engine_gate = engine_payload.get("gate")
        if isinstance(engine_gate, dict) and engine_gate.get("verdict") == "BLOCK":
            reasons.append("engine_strict_gate_block")
    if evidence_level == "schema-only":
        reasons.append("schema_only_evidence")
    payload = {
        "schema_version": GATE_SCHEMA_VERSION,
        "run_id": run_id,
        "evidence_level": evidence_level,
        "sdd_mode": sdd_mode,
        "inputs": inputs,
        "steps": steps,
        "engine": engine_payload,
        "gate": {
            "verdict": "PASS" if not reasons else "BLOCK",
            "reasons": sorted(set(reasons)),
            "blocking_findings": _blocking_findings(engine_payload or {}),
        },
    }
    _write_json(run_dir / "gate-result.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not reasons else GATE_BLOCK_EXIT_CODE


def _run_id(value: Optional[str]) -> str:
    run_id = value or "run-" + uuid.uuid4().hex
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("--run-id must contain only letters, digits, dot, underscore, or dash")
    return run_id


def run_gate(args: argparse.Namespace) -> int:
    project_root = _resolve(args.project_root, Path.cwd())
    no_sdd = bool(getattr(args, "no_sdd", False))
    sdd = _resolve(args.sdd, project_root) if args.sdd else None
    kb = _resolve(args.kb, project_root)
    output_root = _resolve(args.output_dir, project_root)
    targets = [_resolve(item, project_root) for item in args.target]
    run_id = _run_id(args.run_id)

    if not project_root.is_dir():
        raise ValueError("project root does not exist")
    if not no_sdd:
        if not sdd.is_file() or not sdd.read_text(encoding="utf-8").strip():
            raise ValueError("--sdd must be a non-empty file")
        _validate_sdd_ownership(sdd)
    flow_files = _business_flow_files(kb) if kb.is_dir() else []
    if not flow_files:
        raise ValueError("--kb must be a directory containing non-README Markdown business-flow files")
    if any(not target.is_file() for target in targets):
        raise ValueError("every --target must be an existing source file")
    if args.evidence_level == "code-verified" and not args.verify_command:
        raise ValueError("code-verified requires --verify-command against a real public entry")
    if args.verify_command and any(token in args.verify_command for token in SHELL_CONTROL_TOKENS):
        raise ValueError("--verify-command must not contain shell control tokens")
    _validate_changed_files(targets, args.changed_files, project_root)

    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / run_id
    if run_dir.exists():
        raise ValueError("--run-id already exists; use a new id to prevent evidence reuse")
    run_dir.mkdir()

    inputs = {
        "source_sdd_hash": "" if no_sdd else _sha256_file(sdd),
        "source_kb_hash": _sha256_paths(flow_files, kb),
        "target_code_hash": _sha256_paths(targets, project_root),
    }
    steps: Dict[str, Any] = {}
    contract_dir = run_dir / "contract"
    scenario_dir = run_dir / "scenario"

    if no_sdd:
        # 纯前端/纯后端：契约维度显式声明跳过，仍需真实业务验证（不豁免 code-verified）
        contract_dir.mkdir(parents=True, exist_ok=True)
        contract_manifest_payload = {
            "schema_version": "contract-oracle-v1",
            "declared_skipped": True,
            "skip_reason": "no-cross-endpoint",
            "run_id": run_id,
            "source_sdd_hash": "",
            "source_kb_hash": inputs["source_kb_hash"],
            "target_code_hash": inputs["target_code_hash"],
            "evidence_level": args.evidence_level,
        }
        contract_manifest = contract_dir / "oracle-manifest.json"
        _write_json(contract_manifest, contract_manifest_payload)
        steps["contract_extract"] = {
            "command": ["<no-sdd-declared>"],
            "command_sha256": "",
            "exit_code": 0,
            "stdout_sha256": "",
            "stderr_sha256": "",
            "declared_skipped": True,
        }
    else:
        contract_command = [
            sys.executable,
            str(CONTRACT_EXTRACTOR),
            "--sdd",
            str(sdd),
            "--output-dir",
            str(contract_dir),
            "--project-root",
            str(project_root),
        ]
        _, steps["contract_extract"] = _run_command(contract_command, project_root)

    scenario_command = [
        sys.executable,
        str(SCENARIO_EXTRACTOR),
        "--kb",
        str(kb),
        "--output-dir",
        str(scenario_dir),
        "--project-root",
        str(project_root),
    ]
    _, steps["scenario_extract"] = _run_command(scenario_command, project_root)
    failed_extractors = [
        name for name in ("contract_extract", "scenario_extract")
        if steps[name]["exit_code"] != 0
    ]
    if failed_extractors:
        return _gate_result(
            run_dir,
            run_id,
            args.evidence_level,
            inputs,
            steps,
            ["extractor_failed:" + ",".join(failed_extractors)],
            sdd_mode="declared-none" if no_sdd else "declared",
        )

    scenario_manifest = scenario_dir / "scenario-manifest.json"
    contract_manifest = contract_dir / "oracle-manifest.json"
    if not contract_manifest.is_file() or not scenario_manifest.is_file():
        return _gate_result(
            run_dir,
            run_id,
            args.evidence_level,
            inputs,
            steps,
            ["missing_generated_manifest"],
            sdd_mode="declared-none" if no_sdd else "declared",
        )
    try:
        _enrich_manifest(contract_manifest, **inputs, evidence_level=args.evidence_level, run_id=run_id)
        _enrich_manifest(scenario_manifest, **inputs, evidence_level=args.evidence_level, run_id=run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _gate_result(
            run_dir,
            run_id,
            args.evidence_level,
            inputs,
            steps,
            ["invalid_generated_manifest"],
            sdd_mode="declared-none" if no_sdd else "declared",
        )

    generated_tests = sorted(contract_dir.glob("test_*.py")) + sorted(scenario_dir.glob("test_*.py"))
    if not generated_tests:
        return _gate_result(
            run_dir,
            run_id,
            args.evidence_level,
            inputs,
            steps,
            ["no_generated_oracle_tests"],
            sdd_mode="declared-none" if no_sdd else "declared",
        )
    steps["pytest"] = _run_pytest(generated_tests, project_root, run_id)
    shutil.copy2(contract_manifest, run_dir / "oracle-manifest.json")
    shutil.copy2(scenario_manifest, run_dir / "scenario-manifest.json")

    _write_json(run_dir / "pytest-results.json", steps["pytest"])

    verification_failed = False
    if args.verify_command:
        command = shlex.split(args.verify_command)
        if not command:
            raise ValueError("--verify-command must not be empty")
        verify_evidence_path = run_dir / "business-verification.json"
        verification_env = {
            **os.environ,
            "BUSINESS_QUALITY_RUN_ID": run_id,
            "BUSINESS_QUALITY_VERIFY_EVIDENCE": str(verify_evidence_path),
            "BUSINESS_QUALITY_SOURCE_SDD_HASH": inputs["source_sdd_hash"],
            "BUSINESS_QUALITY_SOURCE_KB_HASH": inputs["source_kb_hash"],
            "BUSINESS_QUALITY_TARGET_CODE_HASH": inputs["target_code_hash"],
        }
        _, steps["real_verification"] = _run_command(
            command,
            project_root,
            redact_command=True,
            env=verification_env,
        )
        try:
            verify_evidence = _load_verify_evidence(
                verify_evidence_path,
                inputs,
                run_id,
            )
            steps["real_verification"]["evidence_sha256"] = _sha256_file(
                verify_evidence_path
            )
        except ValueError:
            verification_failed = True
            steps["real_verification"]["evidence_valid"] = False
        else:
            steps["real_verification"]["evidence_valid"] = True
        verification_failed = verification_failed or steps["real_verification"]["exit_code"] != 0

    engine_command = [
        sys.executable,
        str(QUALITY_CHECK),
        "check",
        *[str(target) for target in targets],
        "--profile",
        "code-verified",
        "--oracle-dir",
        str(run_dir),
        "--strict-gate",
        "--format",
        "json",
    ]
    engine_completed, steps["engine"] = _run_command(engine_command, project_root)
    try:
        engine_payload = json.loads(engine_completed.stdout)
        if not isinstance(engine_payload, dict):
            engine_payload = None
    except json.JSONDecodeError:
        engine_payload = None

    reasons: List[str] = []
    if steps["pytest"]["exit_code"] != 0:
        reasons.append("oracle_pytest_failed")
    if verification_failed:
        reasons.append("real_verification_failed")
    if engine_payload is None:
        reasons.append("engine_output_invalid")
    elif engine_completed.returncode not in (0, GATE_BLOCK_EXIT_CODE):
        reasons.append("engine_execution_failed")
    return _gate_result(
        run_dir,
        run_id,
        args.evidence_level,
        inputs,
        steps,
        reasons,
        engine_payload,
        sdd_mode="declared-none" if no_sdd else "declared",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="被验证项目根目录")
    sdd_group = parser.add_mutually_exclusive_group(required=True)
    sdd_group.add_argument("--sdd", help="当前唯一 SDD 契约规范文件（docs/vX.Y.Z/契约规范.md，H1 版本号须与目录一致）")
    sdd_group.add_argument("--no-sdd", action="store_true", help="纯前端/纯后端：显式声明无跨端契约，跳过 contract 生成，仍需 KB + 真实业务验证")
    parser.add_argument("--kb", required=True, help="核心业务流 Markdown 目录")
    parser.add_argument("--target", required=True, nargs="+", help="本轮变更的业务入口源文件")
    parser.add_argument("--changed-files", action="append", default=[], help="本轮 git diff --name-only 结果（可选）；其中业务入口文件必须已列入 --target")
    parser.add_argument("--output-dir", default="tests/oracle", help="业务质量证据根目录")
    parser.add_argument("--run-id", help="本轮证据目录名；缺省时生成不可复用的随机 ID")
    parser.add_argument(
        "--evidence-level",
        choices=("schema-only", "code-verified"),
        default="schema-only",
        help="schema-only 必定 BLOCK；code-verified 必须配合真实入口验证命令",
    )
    parser.add_argument(
        "--verify-command",
        help="调用真实 CLI/HTTP/app-factory/module 入口的无 shell 验证命令；证据写入 BUSINESS_QUALITY_VERIFY_EVIDENCE",
    )
    args = parser.parse_args(argv)
    try:
        return run_gate(args)
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
