"""受控 lesson 晋升器的回归测试。"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "promote_lesson.py"


def _load_promoter():
    spec = importlib.util.spec_from_file_location("promote_lesson_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(tmp_path: Path, *, status: str = "verified", sanitized: bool = True) -> tuple[Path, Path, Path, Path]:
    repo = tmp_path / "repo"
    lesson = repo / ".claude/artifacts/bugs/stream-cleanup/knowledge/lesson.md"
    lesson.parent.mkdir(parents=True)
    kb = repo / "docs/project-note/06-迭代与变更/已知问题与陷阱.md"
    kb.parent.mkdir(parents=True)
    changelog = kb.parent / "变更日志.md"
    gate = repo / "tests/oracle/stream-cleanup/gate-result.json"
    gate.parent.mkdir(parents=True)
    lesson.write_text(
        "---\n"
        f"status: {status}\n"
        f"sanitized: {'true' if sanitized else 'false'}\n"
        "verified_run_id: stream-cleanup\n"
        "---\n"
        "# Bug 修复错题本\n\n"
        "## 模式总结\n流式事件未释放会导致重复渲染。\n\n"
        "## 触发条件\n页面反复切换后重新订阅。\n\n"
        "## 根因模式\n生命周期 cleanup 缺失。\n\n"
        "## 检测规则\n- 搜索 addEventListener 后是否存在 removeEventListener。\n\n"
        "## 标准修复模板\n- 在 cleanup 中关闭订阅。\n",
        encoding="utf-8",
    )
    kb.write_text("# 已知问题与陷阱\n", encoding="utf-8")
    changelog.write_text("# 知识库变更日志\n", encoding="utf-8")
    gate.write_text(
        json.dumps({"run_id": "stream-cleanup", "gate": {"verdict": "PASS"}}),
        encoding="utf-8",
    )
    return repo, lesson, kb, gate


def _args(repo: Path, lesson: Path, kb: Path, gate: Path, **overrides):
    values = {
        "project_root": str(repo),
        "lesson": str(lesson),
        "gate_result": str(gate),
        "kb": str(kb),
        "change_log": None,
        "confirm": True,
        "kb_flow_change": False,
        "run_id": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_promote_writes_summary_and_is_idempotent(tmp_path):
    promoter = _load_promoter()
    repo, lesson, kb, gate = _project(tmp_path)

    first = promoter.promote(_args(repo, lesson, kb, gate))
    second = promoter.promote(_args(repo, lesson, kb, gate))

    assert first["status"] == "promoted"
    assert first["kb_changed"] is True
    audit_path = repo / first["filter_audit"]
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["schema_version"] == "lesson-promotion-filter-audit-v1"
    assert audit["hits"] == {"high": 0, "medium": 0, "low": 0}
    assert "流式事件" not in audit_path.read_text(encoding="utf-8")
    assert second["kb_changed"] is False
    assert kb.read_text(encoding="utf-8").count("lesson-promotion:stream-cleanup") == 1
    assert (kb.parent / "变更日志.md").read_text(encoding="utf-8").count("lesson-promotion:stream-cleanup") == 1
    lesson_text = lesson.read_text(encoding="utf-8")
    assert "status: promoted" in lesson_text
    metadata, _ = promoter._parse_frontmatter(lesson_text)
    assert metadata["kb_entry"] == "docs/project-note/06-迭代与变更/已知问题与陷阱.md#bug-stream-cleanup"


@pytest.mark.parametrize(
    ("status", "sanitized", "message"),
    [
        ("pending", True, "status must be verified"),
        ("rejected", True, "status must be verified"),
        ("verified", False, "sanitized=true"),
    ],
)
def test_promote_rejects_unqualified_lesson(tmp_path, status, sanitized, message):
    promoter = _load_promoter()
    repo, lesson, kb, gate = _project(tmp_path, status=status, sanitized=sanitized)

    with pytest.raises(ValueError, match=message):
        promoter.promote(_args(repo, lesson, kb, gate))

    assert "lesson-promotion:" not in kb.read_text(encoding="utf-8")


def test_promote_requires_verified_run_id(tmp_path):
    promoter = _load_promoter()
    repo, lesson, kb, gate = _project(tmp_path)
    lesson.write_text(
        lesson.read_text(encoding="utf-8").replace("verified_run_id: stream-cleanup\n", ""),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="verified_run_id"):
        promoter.promote(_args(repo, lesson, kb, gate))


def test_promote_requires_matching_pass_gate(tmp_path):
    promoter = _load_promoter()
    repo, lesson, kb, gate = _project(tmp_path)
    gate.write_text(json.dumps({"run_id": "wrong", "gate": {"verdict": "PASS"}}), encoding="utf-8")

    with pytest.raises(ValueError, match="run_id"):
        promoter.promote(_args(repo, lesson, kb, gate))


def test_promote_rejects_non_06_target(tmp_path):
    promoter = _load_promoter()
    repo, lesson, kb, gate = _project(tmp_path)
    other = repo / "docs/project-note/03-核心数据业务流/流.md"
    other.parent.mkdir(parents=True)
    other.write_text("# 业务流\n", encoding="utf-8")

    with pytest.raises(ValueError, match="only permits"):
        promoter.promote(_args(repo, lesson, other, gate))


def test_promote_rejects_business_flow_change_without_new_run(tmp_path):
    promoter = _load_promoter()
    repo, lesson, kb, gate = _project(tmp_path)

    with pytest.raises(ValueError, match="requires --run-id"):
        promoter.promote(_args(repo, lesson, kb, gate, kb_flow_change=True))


def test_promote_blocks_high_severity_content(tmp_path):
    promoter = _load_promoter()
    repo, lesson, kb, gate = _project(tmp_path)
    lesson.write_text(
        lesson.read_text(encoding="utf-8").replace(
            "流式事件未释放会导致重复渲染。", "token=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="high-severity"):
        promoter.promote(_args(repo, lesson, kb, gate))


def test_promote_dry_run_previews_without_writing(tmp_path):
    promoter = _load_promoter()
    repo, lesson, kb, gate = _project(tmp_path)

    result = promoter.promote(_args(repo, lesson, kb, gate, dry_run=True))

    assert result["status"] == "dry-run"
    assert "would_write_kb" in result
    assert "would_write_change_log" in result
    assert "lesson-promotion:stream-cleanup" not in kb.read_text(encoding="utf-8")
    assert "status: promoted" not in lesson.read_text(encoding="utf-8")


def test_migrate_adds_legacy_frontmatter_to_old_lessons(tmp_path):
    promoter = _load_promoter()
    repo, lesson, kb, gate = _project(tmp_path)
    # 还原为无 frontmatter 的历史 lesson（保留正文五段）
    body = lesson.read_text(encoding="utf-8")
    lesson.write_text(body.split("---\n", 2)[2], encoding="utf-8")

    result = promoter.migrate(
        argparse.Namespace(project_root=str(repo), artifacts_dir=".claude/artifacts/bugs")
    )

    assert result["migrated"] == 1
    migrated_text = lesson.read_text(encoding="utf-8")
    assert migrated_text.startswith("---\nstatus: legacy\n---\n")

    # 再跑一次：已有 frontmatter，应跳过
    again = promoter.migrate(
        argparse.Namespace(project_root=str(repo), artifacts_dir=".claude/artifacts/bugs")
    )
    assert again["migrated"] == 0
    assert again["skipped"] == 1
