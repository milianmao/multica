#!/usr/bin/env python3
"""Unified test selection API for the Skill chain.

This is the SINGLE source of truth for "which tests to run given a set of
changed files". Other Skills (deep-planning stage 7.0, fix-bug-workflow Step 3)
call this script instead of inlining their own find/grep logic, so the matching
algorithm lives in exactly one place (D6 fix).

Depth mapping (aligned with test-pyramid-builder Adaptive Test Selection):
  L1 = filename match (P3, ~5s)
  L2 = + same-dir + symbol reference (P2, ~10s)
  L3 = + pyramid layer + mock ceiling (P1, ~30s)
  L4 = + full impact + dependency trace (P0, ~60s)

Usage:
  python3 select_tests.py --changed-files a.py,b.ts --depth L2 --json
  git diff --name-only | python3 select_tests.py --depth L3 --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# File extensions we know how to map to test files
TEST_PREFIXES = ("test_", "test-")
TEST_SUFFIXES = (".test.", ".spec.", "_test.")

# Extensions stripped when deriving module name
SOURCE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".java",
               ".kt", ".swift", ".rs")


def _strip_ext(filename: str) -> str:
    """Strip source extension to get module stem."""
    for ext in SOURCE_EXTS:
        if filename.endswith(ext):
            return filename[: -len(ext)]
    return Path(filename).stem


def _l1_filename_match(changed_files: list[str], repo: Path) -> list[str]:
    """L1: find test files by module name match.

    改了 execution_engine.py -> 找 test_execution*.py / *execution*.spec.*
    """
    selected: set[str] = set()
    for cf in changed_files:
        stem = _strip_ext(Path(cf).name)
        if not stem:
            continue
        # glob for test_<module>* and *<module>*.spec.*
        patterns = [
            f"**/test_{stem}*", f"**/test-{stem}*",
            f"**/{stem}*.spec.*", f"**/{stem}*.test.*",
            f"**/{stem}_test.*",
        ]
        for pat in patterns:
            for hit in repo.glob(pat):
                if hit.is_file() and "node_modules" not in hit.parts:
                    selected.add(str(hit.relative_to(repo)))
    return sorted(selected)


def _l2_symbol_reference(changed_files: list[str], repo: Path, l1_hits: list[str]) -> list[str]:
    """L2: extend with symbol reference search via ripgrep/grep.

    Extracts class/func names from changed files, then finds test files
    referencing those symbols.
    """
    selected: set[str] = set(l1_hits)
    rg = _find_search_binary()
    for cf in changed_files:
        fpath = repo / cf
        if not fpath.exists():
            continue
        symbols = _extract_symbols(fpath)
        if not symbols:
            continue
        for sym in symbols[:10]:  # cap to avoid runaway
            hits = _grep_symbol(sym, repo, rg)
            selected.update(hits)
    return sorted(selected)


def _find_search_binary() -> str:
    """Prefer ripgrep, fall back to grep."""
    for tool in ("rg", "grep"):
        try:
            subprocess.run([tool, "--version"], capture_output=True, check=False)
            return tool
        except FileNotFoundError:
            continue
    return "grep"


def _extract_symbols(fpath: Path) -> list[str]:
    """Extract class/func/def/interface/type names from a source file.

    Handles: Python def/async def/class, JS/TS func/class/export/interface/type,
    Go func/struct/interface, Java/Kotlin method with modifiers.
    Skips short identifiers (<=3 chars) to avoid noise like 'run', 'if'.
    """
    try:
        text = fpath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    symbols: list[str] = []
    import re
    # Match common definition patterns across languages
    # Captures the identifier after the keyword
    pattern = re.compile(
        r'(?:async\s+)?def\s+(\w+)'        # Python def / async def
        r'|class\s+(\w+)'                   # class (Python/JS/TS/Java/Kotlin)
        r'|func\s+(\w+)'                    # Go func / JS func
        r'|interface\s+(\w+)'               # TS/Go interface
        r'|type\s+(\w+)'                    # TS type
        r'|struct\s+(\w+)'                  # Go/Rust struct
        r'|enum\s+(\w+)'                    # TS/Java enum
        r'|export\s+(?:default\s+)?(?:function\s+|class\s+|const\s+|interface\s+|type\s+)?(\w+)'
    )
    for line in text.splitlines():
        stripped = line.lstrip()
        # skip comments
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
            continue
        for m in pattern.finditer(stripped):
            # find the first non-None capture group
            name = next((g for g in m.groups() if g), None)
            if name and len(name) > 3 and name not in {"true", "false", "null", "None", "self"}:
                symbols.append(name)
    return symbols


def _grep_symbol(symbol: str, repo: Path, tool: str) -> list[str]:
    """Find test files referencing a symbol (with word boundary to avoid false matches)."""
    # Skip short/generic symbols to avoid noise (e.g. "run", "if", "test")
    if len(symbol) <= 3 or symbol in {"test", "main", "init", "self", "this", "true", "false"}:
        return []
    test_dirs = _find_test_dirs(repo)
    if not test_dirs:
        return []
    hits: list[str] = []
    if tool == "rg":
        # -w = word boundary, prevents "run" matching "runtime"/"prerun"
        cmd = ["rg", "-l", "-w", "--no-messages", symbol, *test_dirs]
    else:
        # grep -w = word boundary
        cmd = ["grep", "-rlw", "--include=*test*", "--include=*.spec.*",
               symbol, *test_dirs]
    try:
        out = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                             check=False, timeout=15)
        for line in out.stdout.strip().splitlines():
            if line:
                try:
                    rel = str(Path(line).relative_to(repo))
                    hits.append(rel)
                except ValueError:
                    hits.append(line)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return hits


def _is_noise(path_str: str) -> bool:
    """Filter out pycache, pyc, benchmark snapshots, worktrees, artifacts, node_modules."""
    parts = path_str.replace("\\", "/").split("/")
    noise_parts = {"__pycache__", "node_modules", ".venv", "venv", "dist", "build",
                   "artifacts", ".audit", ".git", "vendor", ".idea", ".vscode",
                   "site-packages", "eggs", ".cache", "tmp", "target",
                   ".pytest_cache", ".mypy_cache", ".tox", ".coverage",
                   ".egg-info", "htmlcov", ".nox", ".ruff_cache", ".DS_Store"}
    for p in parts:
        if p in noise_parts:
            return True
        # also match directory names ending with known suffixes (e.g. foo.egg-info)
        for suffix in (".egg-info", ".pyc"):
            if p.endswith(suffix):
                return True
    # skip .pyc and benchmark/snapshot/worktree/artifact copies
    if path_str.endswith(".pyc"):
        return True
    # .claude/ (worktrees, artifacts, memory, etc.)
    if "/.claude/" in path_str or path_str.startswith(".claude/"):
        return True
    if path_str.startswith("benchmark/snapshots/") or path_str.startswith("benchmark/change-backups/"):
        return True
    if "/.audit/" in path_str or path_str.endswith(".audit.json"):
        return True
    return False


def _filter_noise(paths: Iterable[str]) -> list[str]:
    return [p for p in paths if not _is_noise(p)]


def _find_test_dirs(repo: Path) -> list[str]:
    """Locate common test directories."""
    candidates = ["tests", "test", "backend/tests", "frontend/__tests__",
                  "src/__tests__", "__tests__", "spec"]
    return [d for d in candidates if (repo / d).is_dir()]


def _l3_pyramid_check(changed_files: list[str], repo: Path, l2_hits: list[str]) -> list[str]:
    """L3: pyramid layer coverage (lightweight).

    Note: full mock-ceiling detection (mock ratio stats) is NOT implemented here.
    Callers requiring mock-ceiling analysis should invoke test-pyramid-builder's
    full L3 check manually. This function only adds smoke/route tests to the
    selection and flags layers_checked for transparency.
    """
    selected = set(l2_hits)
    # Add smoke/route tests if they exist (common pyramid L1 Shell / L2a Route)
    smoke = repo / "tests" / "smoke_api.sh"
    if smoke.exists():
        selected.add(str(smoke.relative_to(repo)))
    # also pick up existing smoke/route test files already in l2_hits
    return sorted(selected)


def _l4_full_impact(changed_files: list[str], repo: Path, l3_hits: list[str]) -> list[str]:
    """L4: full impact + dependency trace.

    Uses codegraph if available, otherwise falls back to extended grep.
    """
    selected = set(l3_hits)
    # Extended symbol trace: for each changed file, trace callers
    rg = _find_search_binary()
    for cf in changed_files:
        stem = _strip_ext(Path(cf).name)
        if not stem:
            continue
        # find files that import/reference this module
        hits = _grep_symbol(stem, repo, rg)
        selected.update(hits)
    return sorted(selected)


def select(changed_files: list[str], depth: str, repo: Path) -> dict:
    """Run test selection at the given depth.

    Returns:
      { "selected": [...], "skipped_count": N, "depth_used": "L2",
        "layers_checked": [...], "changed_files": [...] }
    """
    depth = depth.upper()
    if depth not in ("L1", "L2", "L3", "L4"):
        raise ValueError(f"invalid depth: {depth} (expected L1/L2/L3/L4)")

    layers_checked: list[str] = []

    l1 = _l1_filename_match(changed_files, repo)
    layers_checked.append("L1-filename")

    if depth == "L1":
        selected = l1
    else:
        l2 = _l2_symbol_reference(changed_files, repo, l1)
        layers_checked.append("L2-symbol")
        if depth == "L2":
            selected = l2
        elif depth == "L3":
            l3 = _l3_pyramid_check(changed_files, repo, l2)
            layers_checked.append("L3-pyramid")
            selected = l3
        else:  # L4
            l3 = _l3_pyramid_check(changed_files, repo, l2)
            layers_checked.append("L3-pyramid")
            l4 = _l4_full_impact(changed_files, repo, l3)
            layers_checked.append("L4-impact")
            selected = l4

    # Deduplicate while preserving order, then filter noise
    seen: set[str] = set()
    deduped: list[str] = []
    for s in selected:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    deduped = _filter_noise(deduped)

    return {
        "selected": deduped,
        "selected_count": len(deduped),
        "depth_used": depth,
        "layers_checked": layers_checked,
        "changed_files": changed_files,
    }


def _parse_changed_files(args: argparse.Namespace) -> list[str]:
    """Parse changed files from --changed-files or stdin."""
    files: list[str] = []
    if args.changed_files:
        files = [f.strip() for f in args.changed_files.split(",") if f.strip()]
    elif not sys.stdin.isatty():
        # read from pipe (e.g. git diff --name-only | select_tests.py)
        files = [line.strip() for line in sys.stdin if line.strip()]
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unified test selection API (D6: single source of truth)"
    )
    parser.add_argument("--changed-files", help="Comma-separated list of changed files")
    parser.add_argument("--depth", default="L2", choices=["L1", "L2", "L3", "L4"],
                        help="Selection depth (default: L2)")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(),
                        help="Repository root (default: cwd)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    changed = _parse_changed_files(args)
    if not changed:
        result = {"selected": [], "selected_count": 0, "depth_used": args.depth,
                  "layers_checked": [], "changed_files": [],
                  "warning": "no changed files provided (use --changed-files or pipe git diff)"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    result = select(changed, args.depth, args.repo_root.resolve())

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"# Test selection (depth={result['depth_used']})")
        print(f"# Changed files: {len(result['changed_files'])}")
        print(f"# Layers checked: {', '.join(result['layers_checked'])}")
        print(f"# Selected: {result['selected_count']} test(s)")
        for t in result["selected"]:
            print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
