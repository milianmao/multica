#!/usr/bin/env python3
"""将已验证且脱敏的 Bug lesson 受控晋升为长期知识库摘要。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple


BUG_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
REQUIRED_SECTIONS = ("模式总结", "触发条件", "根因模式", "检测规则", "标准修复模板")
PROMOTABLE_STATUSES = {"verified", "promoted"}


def _resolve(path: str, project_root: Path) -> Path:
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        return parsed if isinstance(parsed, list) else value
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("lesson.md requires YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("lesson.md frontmatter is not closed")
    metadata: Dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError("lesson.md frontmatter contains an invalid line")
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError("lesson.md frontmatter contains an empty key")
        metadata[key] = _parse_scalar(value)
    return metadata, text[end + len("\n---\n") :]


def _format_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    text = str(value)
    if not text or any(char in text for char in ":#[]{}\n") or text != text.strip():
        return json.dumps(text, ensure_ascii=False)
    return text


def _render_frontmatter(metadata: Dict[str, Any]) -> str:
    return "---\n" + "\n".join(
        f"{key}: {_format_scalar(value)}" for key, value in metadata.items()
    ) + "\n---\n"


def _section(body: str, title: str) -> str:
    match = re.search(rf"^##\s+{re.escape(title)}\s*$", body, re.MULTILINE)
    if not match:
        raise ValueError(f"lesson.md is missing required section: {title}")
    next_heading = re.search(r"^#{1,2}\s+", body[match.end() :], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(body)
    value = body[match.end() : end].strip()
    if not value or value.startswith("["):
        raise ValueError(f"lesson.md section is empty: {title}")
    return value


def _load_gate_result(path: Path, verified_run_id: str) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("gate-result.json must be readable JSON") from exc
    gate = payload.get("gate") if isinstance(payload, dict) else None
    if not isinstance(gate, dict) or gate.get("verdict") != "PASS":
        raise ValueError("gate-result.json must have gate.verdict=PASS")
    if payload.get("run_id") != verified_run_id:
        raise ValueError("gate-result.json run_id must equal lesson verified_run_id")


def _sensitive_filter(text: str, context: str) -> Tuple[str, Dict[str, int]]:
    repo_root = Path(__file__).resolve().parents[3]
    filter_root = repo_root / "skills" / "sensitive-info-filter"
    if not filter_root.is_dir():
        raise ValueError("sensitive-info-filter is unavailable")
    sys.path.insert(0, str(filter_root))
    try:
        from filters.sensitive_filter import SensitiveFilter
    finally:
        sys.path.pop(0)
    result = SensitiveFilter().filter_text(text, context=context)
    if result.has_high:
        raise ValueError("sensitive-info-filter found high-severity content; promotion blocked")
    return result.sanitized, {
        "high": len(result.high_hits),
        "medium": sum(1 for hit in result.hits if hit.severity == "medium"),
        "low": sum(1 for hit in result.hits if hit.severity == "low"),
    }


def _write_audit(lesson_path: Path, bug_name: str, contents: Sequence[str], hits: Sequence[Dict[str, int]]) -> Path:
    audit_dir = lesson_path.parents[1] / ".audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256("\0".join(contents).encode("utf-8")).hexdigest()
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    path = audit_dir / f"promotion-filter-{timestamp}.json"
    payload = {
        "schema_version": "lesson-promotion-filter-audit-v1",
        "bug_name": bug_name,
        "content_sha256": f"sha256:{digest}",
        "hits": {
            "high": sum(item["high"] for item in hits),
            "medium": sum(item["medium"] for item in hits),
            "low": sum(item["low"] for item in hits),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _anchor(bug_name: str) -> str:
    return f"bug-{bug_name}"


def _render_entry(bug_name: str, lesson_path: str, sections: Dict[str, str]) -> str:
    return "\n".join(
        [
            f"<!-- lesson-promotion:{bug_name} -->",
            f"## bug-{bug_name}",
            "",
            f"- **现象**：{sections['模式总结']}",
            f"- **触发条件**：{sections['触发条件']}",
            f"- **根因**：{sections['根因模式']}",
            "- **规避**：",
            sections["检测规则"],
            "",
            "**标准修复模板**：",
            sections["标准修复模板"],
            "",
            f"- **详情**：→ 详见 `{lesson_path}`",
            "",
        ]
    )


def _render_change_log(bug_name: str, kb_path: Path, lesson_path: str) -> str:
    return "\n".join(
        [
            f"<!-- lesson-promotion:{bug_name} -->",
            f"## [KB lesson] - {date.today().isoformat()}",
            "",
            "### 更新",
            f"- `{kb_path.parent.name}/{kb_path.name}`：晋升 `{bug_name}` 的已验证 Bug 模式摘要（详情：`{lesson_path}`）。",
            "",
        ]
    )


def _append_once(path: Path, marker: str, content: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return False
    suffix = "" if text.endswith("\n") else "\n"
    path.write_text(text + suffix + "\n" + content, encoding="utf-8")
    return True


def promote(args: argparse.Namespace) -> Dict[str, Any]:
    project_root = _resolve(args.project_root, Path.cwd())
    lesson_path = _resolve(args.lesson, project_root)
    kb_path = _resolve(args.kb, project_root)
    change_log = _resolve(args.change_log, project_root) if args.change_log else kb_path.parent / "变更日志.md"
    gate_result = _resolve(args.gate_result, project_root)

    if not args.confirm:
        raise ValueError("promotion requires --confirm")
    if args.kb_flow_change:
        if not args.run_id:
            raise ValueError("--kb-flow-change requires --run-id for a new business-quality gate run")
        raise ValueError("--kb-flow-change promotion is deferred to P1; do not modify business-flow KB in P0")
    expected_kb = project_root / "docs" / "project-note" / "06-迭代与变更" / "已知问题与陷阱.md"
    if kb_path != expected_kb:
        raise ValueError("P0 promotion only permits docs/project-note/06-迭代与变更/已知问题与陷阱.md")
    if not lesson_path.is_file():
        raise ValueError("--lesson must be an existing file")
    if not kb_path.is_file():
        raise ValueError("--kb must be an existing knowledge-base file")
    if not change_log.is_file():
        raise ValueError("change log must exist beside --kb")

    try:
        bug_name = lesson_path.parent.parent.name
    except IndexError as exc:
        raise ValueError("lesson path must be under <bug-name>/knowledge/lesson.md") from exc
    if not BUG_NAME_RE.fullmatch(bug_name):
        raise ValueError("lesson bug-name directory must be kebab-case")

    metadata, body = _parse_frontmatter(lesson_path.read_text(encoding="utf-8"))
    status = metadata.get("status")
    if status not in PROMOTABLE_STATUSES:
        raise ValueError("lesson status must be verified or promoted")
    if metadata.get("sanitized") is not True:
        raise ValueError("lesson must have sanitized=true before promotion")
    verified_run_id = metadata.get("verified_run_id")
    if not isinstance(verified_run_id, str) or not verified_run_id:
        raise ValueError("lesson must contain verified_run_id")
    _load_gate_result(gate_result, verified_run_id)

    sections = {title: _section(body, title) for title in REQUIRED_SECTIONS}
    try:
        lesson_rel = lesson_path.relative_to(project_root).as_posix()
        kb_rel = kb_path.relative_to(project_root).as_posix()
    except ValueError:
        raise ValueError("lesson and --kb must be inside --project-root")

    marker = f"<!-- lesson-promotion:{bug_name} -->"
    entry, entry_hits = _sensitive_filter(_render_entry(bug_name, lesson_rel, sections), lesson_rel)
    log_entry, log_hits = _sensitive_filter(_render_change_log(bug_name, kb_path, lesson_rel), lesson_rel)

    if getattr(args, "dry_run", False):
        return {
            "status": "dry-run",
            "lesson": lesson_rel,
            "gate_run_id": verified_run_id,
            "kb_entry": f"{kb_rel}#{_anchor(bug_name)}",
            "would_write_kb": entry,
            "would_write_change_log": log_entry,
        }

    audit_path = _write_audit(lesson_path, bug_name, (entry, log_entry), (entry_hits, log_hits))
    kb_changed = _append_once(kb_path, marker, entry)
    log_changed = _append_once(change_log, marker, log_entry)

    metadata["status"] = "promoted"
    metadata["promoted_at"] = date.today().isoformat()
    metadata["kb_entry"] = f"{kb_rel}#{_anchor(bug_name)}"
    lesson_path.write_text(_render_frontmatter(metadata) + body, encoding="utf-8")

    return {
        "status": "promoted",
        "lesson": lesson_rel,
        "gate_run_id": verified_run_id,
        "kb_entry": metadata["kb_entry"],
        "kb_changed": kb_changed,
        "change_log_changed": log_changed,
        "filter_audit": str(audit_path.relative_to(project_root)),
    }


def migrate(args: argparse.Namespace) -> Dict[str, Any]:
    """为存量 lesson 补 frontmatter（status: legacy），不改已有 frontmatter 的文件。"""
    project_root = _resolve(args.project_root, Path.cwd())
    artifacts_dir = _resolve(args.artifacts_dir, project_root)
    if not artifacts_dir.is_dir():
        raise ValueError("--artifacts-dir must be an existing directory")
    migrated: List[str] = []
    skipped: List[str] = []
    for md in sorted(artifacts_dir.rglob("knowledge/*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.startswith("---\n"):
            skipped.append(str(md.relative_to(project_root)))
            continue
        md.write_text("---\nstatus: legacy\n---\n" + text, encoding="utf-8")
        migrated.append(str(md.relative_to(project_root)))
    return {
        "command": "migrate",
        "migrated": len(migrated),
        "skipped": len(skipped),
        "files": migrated,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--migrate", action="store_true", help="为存量 lesson 补 status: legacy frontmatter")
    parser.add_argument("--artifacts-dir", default=".claude/artifacts/bugs", help="--migrate 时扫描的目录")
    parser.add_argument("--lesson", help="lesson 路径（晋升模式必填）")
    parser.add_argument("--gate-result", help="Phase E gate-result.json 路径")
    parser.add_argument("--kb", help="默认指向 06-迭代与变更/已知问题与陷阱.md")
    parser.add_argument("--change-log", help="默认使用 --kb 同级的 变更日志.md")
    parser.add_argument("--confirm", action="store_true", help="确认写入长期知识库")
    parser.add_argument("--dry-run", action="store_true", help="预览将写入的内容，不落盘")
    parser.add_argument("--kb-flow-change", action="store_true")
    parser.add_argument("--run-id", help="业务流 KB 变更后的新严格门禁 run id")
    args = parser.parse_args(argv)
    try:
        if args.migrate:
            result = migrate(args)
        elif not args.lesson or not args.gate_result or not args.kb:
            parser.error("晋升模式需要 --lesson、--gate-result、--kb")
            return 2
        else:
            result = promote(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
