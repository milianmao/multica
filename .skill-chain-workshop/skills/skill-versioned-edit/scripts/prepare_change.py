#!/usr/bin/env python3
"""Create a pure before-change backup. This command never evaluates a subject."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

try:
    from ._common import (
        find_frozen_file, find_repo_root, json_object, locate_backup, record_path,
        relative_to_repo, resolve_repo_file, sha256_file, validate_change_id,
        validate_targets, write_json_atomic,
    )
except ImportError:
    from _common import (
        find_frozen_file, find_repo_root, json_object, locate_backup, record_path,
        relative_to_repo, resolve_repo_file, sha256_file, validate_change_id,
        validate_targets, write_json_atomic,
    )


def generated_change_id(targets: Sequence[str]) -> str:
    stem = Path(targets[0]).parent.name if len(targets) == 1 else "skill-chain"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stem}-{timestamp}"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Back up SKILL sources before an edit; does not evaluate")
    result.add_argument("--target", action="append", required=True, help="SKILL.md under repository skills/")
    result.add_argument("--change-id")
    result.add_argument("--suite", default="skill-chain-core-v1")
    result.add_argument("--mutations", default="benchmark/mutations/core.json")
    result.add_argument("--repo-root", type=Path)
    return result


def _is_suite(path: Path, suite_id: str) -> bool:
    try:
        return json_object(path).get("id") == suite_id
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _is_mutations(path: Path) -> bool:
    try:
        return isinstance(json_object(path).get("mutations"), list)
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def prepare(args: argparse.Namespace, run=subprocess.run) -> dict:
    repo = (args.repo_root.resolve() if args.repo_root else find_repo_root())
    targets = validate_targets(args.target, repo)
    change_id = validate_change_id(args.change_id or generated_change_id(targets))
    output_record = record_path(repo, change_id)
    if output_record.exists():
        raise FileExistsError(f"change id already exists: {change_id}")
    # Refuse an existing backup even if its record was lost; backup must be immutable.
    for root_name in ("change-backups", "backups", "snapshots"):
        if (repo / "benchmark" / root_name / change_id).exists():
            raise FileExistsError(f"backup already exists for change id: {change_id}")

    suite_source = resolve_repo_file(args.suite, repo, "suites")
    mutation_source = resolve_repo_file(args.mutations, repo, "mutations")
    suite_payload = json_object(suite_source)
    suite_id = suite_payload.get("id")
    if not isinstance(suite_id, str) or not suite_id:
        raise ValueError("suite id is missing")

    command = [
        sys.executable, str(repo / "benchmark" / "run.py"), "backup",
        "--name", change_id, "--subject", "current",
        "--suite", str(suite_source), "--mutations", str(mutation_source),
    ]
    completed = run(command, cwd=repo, text=True, capture_output=True, check=False)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "benchmark backup failed").strip()
        raise RuntimeError(detail)

    backup = (repo / "benchmark" / "change-backups" / change_id).resolve()
    if not backup.is_dir():
        raise ValueError(f"backup not found at required path: {backup}")
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from benchmark.scripts.backup import load_backup
    try:
        loaded = load_backup(backup)
    except Exception:
        raise
    manifest = loaded["manifest"]
    if manifest.get("evaluation_status") != "deferred":
        raise ValueError("backup evaluation status must be exactly deferred")
    forbidden_names = {"results.json", "score.json", "mutations.json", "assessment.json", "assessment.md"}
    forbidden = [path for path in backup.rglob("*") if path.is_file() and path.name in forbidden_names]
    if forbidden:
        raise ValueError(f"backup contains evaluation artifact: {forbidden[0]}")
    subject = backup / "subject"
    frozen_suite = backup / str(manifest["suite_path"])
    frozen_mutations = backup / str(manifest["mutations_path"])
    record = {
        "schema_version": 1,
        "change_id": change_id,
        "status": "prepared",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "targets": targets,
        "before": {
            "backup_path": relative_to_repo(backup, repo),
            "subject": f"path:{relative_to_repo(subject, repo)}",
            "manifest": relative_to_repo(backup / "manifest.json", repo),
            "subject_hash": manifest.get("subject_hash"),
        },
        "frozen_evaluation": {
            "suite_id": suite_id,
            "suite_hash": manifest.get("suite_hash", suite_payload.get("suite_hash")),
            "suite_path": relative_to_repo(frozen_suite, repo),
            "suite_file_hash": sha256_file(frozen_suite),
            "mutation_path": relative_to_repo(frozen_mutations, repo),
            "mutation_file_hash": sha256_file(frozen_mutations),
            "mutation_set_hash": manifest.get("mutation_set_hash"),
        },
        "evaluation": {"status": "deferred"},
    }
    write_json_atomic(output_record, record, exclusive=True)
    return record


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = prepare(args)
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    print("Backup integrity: PASS\nEvaluation executed: NO\nSAFE TO EDIT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
