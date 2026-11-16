#!/usr/bin/env python3
"""Explicitly evaluate a prepared before/after SKILL change using frozen criteria."""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from ._common import find_repo_root, json_object, record_path, validate_change_id, write_json_atomic
except ImportError:
    from _common import find_repo_root, json_object, record_path, validate_change_id, write_json_atomic


_REQUIRED_MUTATION_FIELDS = {
    "baseline", "detection_rate", "invalid", "total", "valid", "detected",
    "mutation_set_hash", "mutation_ids",
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Evaluate a completed SKILL edit against its frozen Before backup")
    result.add_argument("--change-id", required=True)
    result.add_argument("--retry", action="store_true", help="start a fresh attempt after an interrupted/failed evaluation")
    result.add_argument("--repo-root", type=Path)
    return result


def _run(command: list[str], repo: Path, runner) -> Any:
    return runner(command, cwd=repo, text=True, capture_output=True, check=False)


def _load_trusted_backup(repo: Path, change_id: str) -> dict[str, Any]:
    backup_path = repo / "benchmark" / "change-backups" / change_id
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from benchmark.scripts.backup import load_backup
    return load_backup(backup_path)


def _expected_record_paths(repo: Path, backup: Path, manifest: Mapping[str, Any]) -> dict[str, str]:
    subject = backup / "subject"
    suite = backup / str(manifest["suite_path"])
    mutations = backup / str(manifest["mutations_path"])
    return {
        "backup_path": backup.relative_to(repo).as_posix(),
        "subject": f"path:{subject.relative_to(repo).as_posix()}",
        "suite_path": suite.relative_to(repo).as_posix(),
        "mutation_path": mutations.relative_to(repo).as_posix(),
    }


def _validate_record_binding(record: Mapping[str, Any], repo: Path, loaded: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    backup = Path(loaded["path"])
    manifest = loaded["manifest"]
    expected = _expected_record_paths(repo, backup, manifest)
    before = record.get("before")
    frozen = record.get("frozen_evaluation")
    if not isinstance(before, Mapping) or not isinstance(frozen, Mapping):
        raise ValueError("change record is missing before/frozen_evaluation")
    checks = {
        "before.backup_path": (before.get("backup_path"), expected["backup_path"]),
        "before.subject": (before.get("subject"), expected["subject"]),
        "before.subject_hash": (before.get("subject_hash"), manifest.get("subject_hash")),
        "frozen_evaluation.suite_path": (frozen.get("suite_path"), expected["suite_path"]),
        "frozen_evaluation.mutation_path": (frozen.get("mutation_path"), expected["mutation_path"]),
        "frozen_evaluation.suite_hash": (frozen.get("suite_hash"), manifest.get("suite_hash")),
        "frozen_evaluation.mutation_set_hash": (frozen.get("mutation_set_hash"), manifest.get("mutation_set_hash")),
    }
    for label, (actual, wanted) in checks.items():
        if actual != wanted:
            raise ValueError(f"change record redirects away from sealed backup: {label}")
    return backup / "subject", backup / str(manifest["suite_path"]), backup / str(manifest["mutations_path"])


def _next_attempt(record: Mapping[str, Any]) -> int:
    attempts = record.get("attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("change record attempts must be a list")
    return len(attempts) + 1


def _validate_mutation_result(path: Path, manifest: Mapping[str, Any], expected_subject_hash: str | None) -> dict[str, Any]:
    payload = json_object(path)
    missing = sorted(_REQUIRED_MUTATION_FIELDS.difference(payload))
    if missing:
        raise ValueError(f"mutation result missing field: {missing[0]}")
    if payload["mutation_set_hash"] != manifest["mutation_set_hash"]:
        raise ValueError("mutation result set hash does not match frozen backup")
    baseline = payload["baseline"]
    if not isinstance(baseline, dict):
        raise ValueError("mutation baseline must be an object")
    if baseline.get("suite_id") != manifest["suite_id"] or baseline.get("suite_hash") != manifest["suite_hash"]:
        raise ValueError("mutation baseline suite does not match frozen backup")
    if expected_subject_hash is not None and baseline.get("subject_hash") != expected_subject_hash:
        raise ValueError("Before mutation subject does not match sealed backup")
    for field in ("total", "valid", "invalid", "detected"):
        if type(payload[field]) is not int or payload[field] < 0:
            raise ValueError(f"mutation result {field} must be a non-negative integer")
    if not isinstance(payload["mutation_ids"], list):
        raise ValueError("mutation result mutation_ids must be a list")
    normalized = copy.deepcopy(baseline)
    normalized["mutation"] = payload
    return normalized


def evaluate(args: argparse.Namespace, run=subprocess.run) -> dict[str, Any]:
    repo = args.repo_root.resolve() if args.repo_root else find_repo_root()
    change_id = validate_change_id(args.change_id)
    path = record_path(repo, change_id)
    if not path.is_file():
        raise FileNotFoundError(f"change id not found: {change_id}")
    record = json_object(path)
    if record.get("change_id") != change_id:
        raise ValueError("change record id mismatch")

    status = record.get("status")
    evaluation_status = record.get("evaluation", {}).get("status") if isinstance(record.get("evaluation"), dict) else None
    retry = bool(getattr(args, "retry", False))
    if status == "assessed" or evaluation_status == "completed":
        raise ValueError(f"change id is not awaiting evaluation: {change_id}")
    if status == "prepared" and evaluation_status == "deferred":
        if retry:
            raise ValueError("--retry is only valid after an interrupted or failed evaluation")
    elif status in {"evaluating", "evaluation_failed"} and evaluation_status in {"running", "failed"}:
        if not retry:
            raise ValueError("interrupted/failed evaluation requires --retry")
    else:
        raise ValueError(f"change id is not awaiting evaluation: {change_id}")

    loaded = _load_trusted_backup(repo, change_id)
    manifest = loaded["manifest"]
    subject_path, suite, mutations = _validate_record_binding(record, repo, loaded)

    attempt_number = _next_attempt(record)
    attempt_id = f"attempt-{attempt_number:03d}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    prefix = f"{change_id}-{attempt_id}"
    before_run, after_run, assess_run = f"{prefix}-before", f"{prefix}-after", f"{prefix}-assessment"
    result_root = repo / "benchmark" / "results"
    for run_id in (before_run, after_run, assess_run):
        if (result_root / run_id).exists():
            raise FileExistsError(f"fresh evaluation output already exists: {run_id}")

    started = datetime.now(timezone.utc).isoformat()
    attempt = {
        "attempt_id": attempt_id, "status": "running", "started_at": started,
        "before_run": before_run, "after_run": after_run, "assessment_run": assess_run,
    }
    record.setdefault("attempts", []).append(attempt)
    record["status"] = "evaluating"
    record["evaluation"] = {"status": "running", "attempt_id": attempt_id, "started_at": started}
    write_json_atomic(path, record)

    python, runner = sys.executable, str(repo / "benchmark" / "run.py")
    try:
        commands = [
            [python, runner, "mutation", "--run-id", before_run, "--subject", f"path:{subject_path}", "--suite", str(suite), "--mutations", str(mutations)],
            [python, runner, "mutation", "--run-id", after_run, "--subject", "current", "--suite", str(suite), "--mutations", str(mutations)],
        ]
        mutation_paths = [result_root / before_run / "mutations.json", result_root / after_run / "mutations.json"]
        normalized_paths = [result_root / before_run / "assessment-source.json", result_root / after_run / "assessment-source.json"]
        for index, (command, output, normalized_path) in enumerate(zip(commands, mutation_paths, normalized_paths)):
            completed = _run(command, repo, run)
            if not output.is_file():
                detail = (completed.stderr or completed.stdout or "mutation evaluation failed").strip()
                raise RuntimeError(detail)
            normalized = _validate_mutation_result(output, manifest, manifest["subject_hash"] if index == 0 else None)
            write_json_atomic(normalized_path, normalized, exclusive=True)

        assess_command = [python, runner, "assess", "--run-id", assess_run, "--before", str(normalized_paths[0]), "--after", str(normalized_paths[1])]
        assessed = _run(assess_command, repo, run)
        assessment_path = result_root / assess_run / "assessment.json"
        if not assessment_path.is_file():
            detail = (assessed.stderr or assessed.stdout or "strict assessment failed").strip()
            raise RuntimeError(detail)
        assessment = json_object(assessment_path)
        verdict = assessment.get("verdict")
        if verdict not in {"enhanced", "unchanged", "mixed", "regressed"}:
            raise ValueError(f"invalid assessment verdict: {verdict}")
        attempt.update(status="completed", completed_at=datetime.now(timezone.utc).isoformat(), verdict=verdict)
        record["status"] = "assessed"
        record["evaluation"] = {
            "status": "completed", "attempt_id": attempt_id, "started_at": started,
            "completed_at": attempt["completed_at"], "before_run": before_run, "after_run": after_run,
            "assessment_run": assess_run, "before_result": normalized_paths[0].relative_to(repo).as_posix(),
            "after_result": normalized_paths[1].relative_to(repo).as_posix(),
            "assessment": assessment_path.relative_to(repo).as_posix(), "verdict": verdict,
            "strict_comparison": True,
        }
        write_json_atomic(path, record)
        return record
    except BaseException as error:
        failed_at = datetime.now(timezone.utc).isoformat()
        attempt.update(status="failed", failed_at=failed_at, error=str(error))
        record["status"] = "evaluation_failed"
        record["evaluation"] = {"status": "failed", "attempt_id": attempt_id, "started_at": started, "failed_at": failed_at, "error": str(error)}
        write_json_atomic(path, record)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = evaluate(args)
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["evaluation"]["verdict"] == "regressed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
