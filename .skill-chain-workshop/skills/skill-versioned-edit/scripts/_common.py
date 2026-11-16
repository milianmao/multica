"""Shared helpers for the versioned SKILL edit workflow."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

_CHANGE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "benchmark" / "run.py").is_file() and (candidate / "skills").is_dir():
            return candidate
    raise ValueError("cannot locate repository root containing benchmark/run.py and skills/")


def validate_change_id(value: str) -> str:
    if not isinstance(value, str) or not _CHANGE_ID.fullmatch(value):
        raise ValueError("change id must be 1-128 safe characters: letters, digits, '.', '_' or '-'")
    return value


def safe_relative(path: Path, root: Path, label: str) -> Path:
    resolved_root = root.resolve()
    candidate = path if path.is_absolute() else resolved_root / path
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    resolved = candidate.resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise ValueError(f"{label} escapes allowed root: {path}")
    return resolved


def validate_targets(values: Iterable[str], repo_root: Path) -> list[str]:
    skills_root = (repo_root / "skills").resolve()
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        requested = Path(raw)
        if requested.is_absolute():
            target = safe_relative(requested, skills_root, "target")
        elif requested.parts and requested.parts[0] == "skills":
            target = safe_relative(repo_root / requested, skills_root, "target")
        else:
            target = safe_relative(requested, skills_root, "target")
        if target.name != "SKILL.md" or not target.is_file():
            raise ValueError(f"target must be an existing SKILL.md under skills/: {raw}")
        relative = target.relative_to(repo_root.resolve()).as_posix()
        if relative in seen:
            raise ValueError(f"duplicate target: {relative}")
        seen.add(relative)
        result.append(relative)
    if not result:
        raise ValueError("at least one --target is required")
    return result


def resolve_repo_file(value: str, repo_root: Path, default_dir: str, suffix: str = ".json") -> Path:
    raw = Path(value)
    candidates = [raw if raw.is_absolute() else repo_root / raw]
    if len(raw.parts) == 1:
        candidates.insert(0, repo_root / "benchmark" / default_dir / f"{value}{suffix}")
    for candidate in candidates:
        if candidate.is_file():
            resolved = candidate.resolve()
            if repo_root.resolve() not in resolved.parents:
                raise ValueError(f"file escapes repository: {value}")
            return resolved
    raise FileNotFoundError(f"file not found: {value}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def write_json_atomic(path: Path, payload: Mapping[str, Any], exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if exclusive:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
        return
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def record_path(repo_root: Path, change_id: str) -> Path:
    return repo_root / "benchmark" / "change-records" / f"{validate_change_id(change_id)}.json"


def _manifest_backup_path(manifest: Mapping[str, Any], repo_root: Path) -> Path | None:
    for key in ("backup_path", "path", "output"):
        value = manifest.get(key)
        if isinstance(value, str) and value:
            path = Path(value)
            return (path if path.is_absolute() else repo_root / path).resolve()
    return None


def locate_backup(repo_root: Path, change_id: str) -> Path:
    roots = (
        repo_root / "benchmark" / "change-backups",
        repo_root / "benchmark" / "backups",
        repo_root / "benchmark" / "snapshots",
    )
    direct = [root / change_id for root in roots]
    matches = [path.resolve() for path in direct if path.is_dir()]
    if not matches:
        for manifest_path in (repo_root / "benchmark").glob("**/manifest.json"):
            if manifest_path.parent.name != change_id:
                continue
            candidate = manifest_path.parent.resolve()
            if (repo_root / "benchmark").resolve() in candidate.parents:
                matches.append(candidate)
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise ValueError(f"expected exactly one backup for {change_id}, found {len(unique)}")
    return unique[0]


def find_frozen_file(backup: Path, relative_candidates: Iterable[str], predicate) -> Path:
    for relative in relative_candidates:
        candidate = backup / relative
        if candidate.is_file() and predicate(candidate):
            return candidate.resolve()
    matches = [path.resolve() for path in backup.rglob("*.json") if predicate(path)]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one frozen file in {backup}, found {len(matches)}")
    return matches[0]


def relative_to_repo(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()
