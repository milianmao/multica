"""
test_query_knowledge.py — fixture-based 端到端测试

测试在 tmp_path 里 mock 一个最小项目（KB + codegraph + predev artifacts），
不依赖任何真实项目文件，可在 CI 里跑。

参数位置约定（argparse 全局参数必须放子命令前）：
  python query_knowledge.py --repo REPO --backend X --json --top N CMD [args]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# 让 conftest 的工厂函数可被导入
sys.path.insert(0, str(Path(__file__).parent))
from conftest import build_full_repo, build_minimal_kb, build_minimal_codegraph  # noqa: E402

# 动态定位脚本：优先仓库内（开发/CI），fallback 到 ~/.claude/skills/（用户安装）
_REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/ -> query-knowledge/ -> skills/ -> repo_root
_SCRIPT_CANDIDATES = [
    _REPO_ROOT / "skills/query-knowledge/scripts/query_knowledge.py",
    Path.home() / ".claude/skills/query-knowledge/scripts/query_knowledge.py",
]
SCRIPT = next((p for p in _SCRIPT_CANDIDATES if p.exists()), _SCRIPT_CANDIDATES[0])


def run(repo: Path, *args: str) -> tuple[int, str, str]:
    """调用 CLI，返回 (returncode, stdout, stderr)"""
    proc = subprocess.run(
        ["python3", str(SCRIPT), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_cwd(cwd: Path, *args: str) -> tuple[int, str, str]:
    """在指定 cwd 下调用 CLI（不显式传 --repo，模拟 subagent 默认行为）"""
    proc = subprocess.run(
        ["python3", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    return proc.returncode, proc.stdout, proc.stderr


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"❌ FAIL: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"✅ {msg}")


# ────────────────────────── [A] project-note 后端 ──────────────────────────

def test_backend_project_note_basic(full_repo: Path):
    print("\n═══ [A] project-note 后端基础 ═══")
    rc, out, _ = run(full_repo, "--backend", "project-note", "stats")
    must(rc == 0, "stats 返回 0")
    data = json.loads(out)
    # build_full_repo 建了 5 个 .md（README + 01-架构/README + 01-架构/分层架构 + 02-功能/README + 02-功能/多主题切换）
    must(data["total_docs"] == 5, f"total_docs=5 (实际={data['total_docs']})")
    must(data["by_layer"]["L1"] == 1, f"L1=1 (实际={data['by_layer']['L1']})")
    must(data["by_layer"]["L2"] == 2, f"L2=2 (实际={data['by_layer']['L2']})")
    must(data["by_layer"]["L3"] == 2, f"L3=2 (实际={data['by_layer']['L3']})")

    rc, out, _ = run(full_repo, "--backend", "project-note", "list")
    data = json.loads(out)
    must("01-架构" in data["categories"], f"list 含 01-架构 (实际={data['categories']})")

    rc, out, _ = run(full_repo, "--backend", "project-note", "overview")
    data = json.loads(out)
    must(data["total_docs"] == 5, f"overview total_docs=5")

    rc, out, _ = run(full_repo, "--backend", "project-note", "hierarchy")
    data = json.loads(out)
    must("01-架构" in data["hierarchy"], "hierarchy 含 01-架构")

    rc, out, _ = run(full_repo, "--backend", "project-note", "category", "01-架构")
    data = json.loads(out)
    must(data["category"] == "01-架构", "category 名正确")

    rc, out, _ = run(full_repo, "--backend", "project-note", "read", "docs/project-note/README.md")
    data = json.loads(out)
    must("Test Project KB" in data["content"], "read README 正确返回内容")


def test_backend_project_note_search(full_repo: Path):
    print("\n═══ [A] project-note search 评分 ═══")
    rc, out, _ = run(
        full_repo, "--backend", "project-note", "--json",
        "--top", "3", "--context", "feature",
        "search", "主题切换",
    )
    must(rc == 0, "search 返回 0")
    data = json.loads(out)
    must(data["hit_count"] >= 1, f"主题切换 至少命中 1 条 (实际={data['hit_count']})")
    top1 = data["hits"][0]
    # 多主题切换.md 应该排第一（标题命中）
    must("多主题切换" in top1["path"], f"top1 应是多主题切换.md (实际={top1['path']})")
    must(top1["score"] >= 0.7, f"top1 score ≥ 0.7 (实际={top1['score']})")

    # 多关键词
    rc, out, _ = run(
        full_repo, "--backend", "project-note", "--json",
        "--top", "3",
        "search", "前端 后端",
    )
    data = json.loads(out)
    must(data["hit_count"] >= 1, f"前端 后端 至少命中 1 条 (实际={data['hit_count']})")


def test_backend_project_note_l1_protection(full_repo: Path):
    print("\n═══ [A] L1 README 防膨胀 ═══")
    rc, out, _ = run(
        full_repo, "--backend", "project-note", "--json",
        "search", "Test Project KB",
    )
    data = json.loads(out)
    # L1 即使命中也应排尾部
    if data["hits"]:
        last_layer = data["hits"][-1]["layer"]
        must(last_layer == "L1", f"L1 应排尾部 (最后一层={last_layer})")


# ────────────────────────── [B] codegraph 后端 ──────────────────────────

def test_backend_codegraph_stats(full_repo: Path):
    print("\n═══ [B] codegraph 后端 stats ═══")
    rc, out, _ = run(full_repo, "--backend", "codegraph", "--json", "cg-stats")
    must(rc == 0, "cg-stats 返回 0")
    data = json.loads(out)
    must(data["nodes"] == 3, f"nodes=3 (实际={data['nodes']})")
    must(data["edges"] == 2, f"edges=2 (实际={data['edges']})")
    must(data["files"] == 2, f"files=2 (实际={data['files']})")


def test_backend_codegraph_search(full_repo: Path):
    print("\n═══ [B] codegraph 后端 search ═══")
    rc, out, _ = run(
        full_repo, "--backend", "codegraph", "--json",
        "--limit", "3",
        "cg-search", "Handler",
    )
    must(rc == 0, "cg-search 返回 0")
    data = json.loads(out)
    must(len(data["results"]) >= 1, f"至少 1 条结果 (实际={len(data['results'])})")
    first = data["results"][0]
    must("qualified_name" in first, "结果含 qualified_name")
    must("file" in first, "结果含 file")

    # --kind 过滤
    rc, out, _ = run(
        full_repo, "--backend", "codegraph", "--json",
        "--kind", "function", "--limit", "5",
        "cg-search", "Handler",
    )
    data = json.loads(out)
    must(rc == 0, "cg-search with --kind 返回 0")
    if data["results"]:
        kinds = {r["kind"] for r in data["results"]}
        must(kinds == {"function"}, f"--kind=function 过滤生效 (实际 kinds={kinds})")


def test_backend_codegraph_callers(full_repo: Path):
    print("\n═══ [B] codegraph 后端 callers/callees/impact ═══")
    rc, out, _ = run(
        full_repo, "--backend", "codegraph", "--json",
        "cg-callers", "_memory_auth",
    )
    must(rc == 0, "cg-callers 返回 0")
    data = json.loads(out)
    must("callers" in data, "返回 callers 字段")
    # Handler.do_GET 调用了 _memory_auth
    callers_list = [c["caller"] for c in data["callers"]]
    must("Handler.do_GET" in callers_list, f"Handler.do_GET 应在 callers 里 (实际={callers_list})")

    rc, out, _ = run(
        full_repo, "--backend", "codegraph", "--json",
        "cg-callees", "Handler.do_GET",
    )
    data = json.loads(out)
    must("callees" in data, "返回 callees 字段")

    rc, out, _ = run(
        full_repo, "--backend", "codegraph", "--json",
        "cg-impact", "Handler.do_GET",
    )
    data = json.loads(out)
    must("callers_count" in data, "返回 callers_count 字段")
    must("affected_files" in data, "返回 affected_files 字段")


def test_backend_codegraph_missing_symbol(full_repo: Path):
    print("\n═══ [B] codegraph 不存在的符号 ═══")
    rc, out, _ = run(
        full_repo, "--backend", "codegraph", "--json",
        "cg-callers", "NotExist.fake",
    )
    must(rc == 0, "不存在的符号 returncode=0")
    data = json.loads(out)
    must(data["callers"] == [], "callers 应为空列表")


def test_backend_codegraph_schema_too_new(full_repo: Path):
    print("\n═══ [B] codegraph schema 太新 → 提醒更新 query-knowledge ═══")
    # 把 schema_versions 改成 v99（比 SUPPORTED_SCHEMA_VERSION=4 新）
    import sqlite3
    db_path = full_repo / ".codegraph" / "codegraph.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE schema_versions SET version = 99 WHERE version = 4")
    conn.commit()
    conn.close()

    rc, out, err = run(full_repo, "--backend", "codegraph", "--json", "cg-stats")
    must(rc == 0, "schema 太新时 returncode=0（优雅降级，不崩）")
    data = json.loads(out)
    must("error" in data or "unavailable" in data.get("reason", "") or "更新 query-knowledge" in data.get("reason", ""),
         f"reason 应提示更新 query-knowledge (实际={data})")
    # stderr 应有醒目提醒
    must("更新 query-knowledge" in err or "query-knowledge" in err.lower(),
         f"stderr 应有醒目提醒 (err={err[:100]})")


def test_backend_codegraph_schema_too_old(full_repo: Path):
    print("\n═══ [B] codegraph schema 太老 → 提醒重建 codegraph ═══")
    # 把 schema_versions 改成 v0（比 MIN_SCHEMA_VERSION=1 老）
    import sqlite3
    db_path = full_repo / ".codegraph" / "codegraph.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE schema_versions SET version = 0 WHERE version = 4")
    conn.commit()
    conn.close()

    rc, out, _ = run(full_repo, "--backend", "codegraph", "--json", "cg-stats")
    must(rc == 0, "schema 太老时 returncode=0")
    data = json.loads(out)
    reason = data.get("reason", "")
    must("重建" in reason or "codegraph 索引太老" in reason,
         f"reason 应提示重建 codegraph (实际={reason[:80]})")


# ────────────────────────── [C] predev 后端 ──────────────────────────

def test_backend_predev_scan(full_repo: Path):
    print("\n═══ [C] predev 后端 pd-scan ═══")
    rc, out, _ = run(
        full_repo, "--backend", "predev", "--json",
        "--layer", "backend", "--tech", "SSE",
        "pd-scan",
    )
    must(rc == 0, "pd-scan 返回 0")
    data = json.loads(out)
    must("matched_rules" in data, "返回 matched_rules 字段")
    must("checklist" in data, "返回 checklist 字段")
    rule_names = {r["rule"] for r in data["matched_rules"]}
    must("SSE / 流式" in rule_names, f"匹配到 SSE 规则 (实际={rule_names})")


def test_backend_predev_empty_scope(full_repo: Path):
    print("\n═══ [C] predev 空 scope ═══")
    rc, out, _ = run(full_repo, "--backend", "predev", "--json", "pd-scan")
    must(rc == 0, "空 scope pd-scan 返回 0")
    data = json.loads(out)
    must(data["matched_rules_count"] == 0, "无匹配规则")


def test_backend_predev_artifacts_scan(full_repo: Path):
    print("\n═══ [C] predev 扫描 artifacts 目录 ═══")
    # full_repo 里有 .claude/artifacts/bugs/test-bug-001/knowledge/lesson.md
    # 内容包含 addEventListener，应被前端组件生命周期规则匹配
    rc, out, _ = run(
        full_repo, "--backend", "predev", "--json",
        "--tech", "addEventListener",
        "pd-scan",
    )
    data = json.loads(out)
    must(data["knowledge_files_count"] >= 1,
         f"应扫到 1 份知识文件 (实际={data['knowledge_files_count']})")


def test_backend_predev_exclude_statuses_configurable(full_repo: Path):
    print("\n═══ [C] predev exclude_statuses 可配置 ═══")
    root = full_repo / ".claude/artifacts/bugs"
    rejected = root / "rejected-custom/knowledge/lesson.md"
    rejected.parent.mkdir(parents=True)
    rejected.write_text(
        "---\nstatus: rejected\n---\n# lesson\nSSE addEventListener cleanup\n",
        encoding="utf-8",
    )
    # 配置只排除 superseded，不排除 rejected → rejected 应回到活跃命中
    (full_repo / ".query-knowledge.yml").write_text(
        "backends:\n  predev:\n    exclude_statuses:\n      - superseded\n",
        encoding="utf-8",
    )

    rc, out, _ = run(full_repo, "--backend", "predev", "--json", "--tech", "SSE", "pd-scan")
    must(rc == 0, "自定义 exclude_statuses 的 pd-scan 返回 0")
    data = json.loads(out)
    must(any("rejected-custom" in item["path"] for item in data["knowledge_files"]),
         "配置排除 superseded 后 rejected 应命中")


# ────────────────────────── [D] --backend=all 聚合 ──────────────────────────

def test_backend_all_aggregated(full_repo: Path):
    print("\n═══ [D] --backend=all 聚合模式 ═══")
    rc, out, _ = run(
        full_repo, "--backend", "all", "--json",
        "--top", "2", "--tech", "SSE",
        "search", "Handler",
    )
    must(rc == 0, "--backend=all 返回 0")
    data = json.loads(out)
    must("backends" in data, "返回 backends 字段")
    must("project-note" in data["backends"], "含 project-note 子结果")
    must("codegraph" in data["backends"], "含 codegraph 子结果")
    must("predev" in data["backends"], "含 predev 子结果")


def test_backend_all_graceful_degradation(tmp_path: Path):
    print("\n═══ [D] --backend=all 优雅降级（无 codegraph）═══")
    # 只建 KB，不建 codegraph
    build_minimal_kb(tmp_path)
    rc, out, _ = run(
        tmp_path, "--backend", "all", "--json",
        "search", "test",
    )
    must(rc == 0, "无 codegraph 时 --backend=all 不崩")
    data = json.loads(out)
    must("codegraph" in data["backends"], "codegraph 字段存在（即使不可用）")
    codegraph_result = data["backends"]["codegraph"]
    must("error" in codegraph_result or "skipped" in codegraph_result,
         f"codegraph 应优雅降级 (实际={list(codegraph_result.keys())})")
    must("project-note" in data["backends"], "project-note 仍正常返回")


# ────────────────────────── [E] 配置文件 ──────────────────────────

def test_config_file_override(tmp_path: Path):
    print("\n═══ [E] .query-knowledge.yml 配置覆盖 ═══")
    # 建一个非标准路径的 KB
    custom_kb = tmp_path / "docs" / "my-custom-kb"
    custom_kb.mkdir(parents=True)
    (custom_kb / "README.md").write_text("# Custom KB\n\n## 关键事实\n- 自定义路径\n", encoding="utf-8")
    (custom_kb / "01-test").mkdir()
    (custom_kb / "01-test" / "doc.md").write_text("# Test\n", encoding="utf-8")

    # 写 .query-knowledge.yml（用 JSON 替代，避免 PyYAML 依赖）
    import json as _json
    config = {
        "backends": {
            "project-note": {
                "kb_path": "docs/my-custom-kb",
            }
        }
    }
    (tmp_path / ".query-knowledge.json").write_text(_json.dumps(config), encoding="utf-8")

    rc, out, _ = run(tmp_path, "--backend", "project-note", "--json", "stats")
    must(rc == 0, "自定义 KB 路径 stats 返回 0")
    data = json.loads(out)
    must(data["total_docs"] == 2, f"应识别自定义路径 2 个文档 (实际={data['total_docs']})")


def test_config_auto_detect(tmp_path: Path):
    print("\n═══ [E] auto-detect KB 路径 ═══")
    # 建一个 docs/kb/ 路径（在候选列表里）
    kb = tmp_path / "docs" / "kb"
    kb.mkdir(parents=True)
    (kb / "README.md").write_text("# Auto-detected KB\n", encoding="utf-8")

    rc, out, _ = run(tmp_path, "--backend", "project-note", "--json", "stats")
    must(rc == 0, "auto-detect stats 返回 0")
    data = json.loads(out)
    must(data["total_docs"] == 1, f"应 auto-detect 到 docs/kb/ (实际={data['total_docs']})")


# ────────────────────────── [F] 边界情况 ──────────────────────────

def test_json_parseable_all_backends(full_repo: Path):
    print("\n═══ [F] JSON 输出可被解析（所有后端）═══")
    cmds = [
        ["--backend", "project-note", "stats"],
        ["--backend", "project-note", "overview"],
        ["--backend", "project-note", "list"],
        ["--backend", "project-note", "hierarchy"],
        ["--backend", "project-note", "category", "01-架构"],
        ["--backend", "codegraph", "cg-stats"],
        ["--backend", "predev", "pd-scan"],
    ]
    for cmd in cmds:
        rc, out, _ = run(full_repo, "--json", *cmd)
        try:
            json.loads(out)
            print(f"✅ {' '.join(cmd[:4])} --json 可解析")
        except json.JSONDecodeError as e:
            must(False, f"{' '.join(cmd)} --json 不可解析: {e}")


def test_missing_kb_graceful(tmp_path: Path):
    print("\n═══ [F] 无 KB 项目应优雅报错 ═══")
    # 空目录，没有任何 KB
    rc, out, err = run(tmp_path, "--backend", "project-note", "stats")
    must(rc == 2, f"无 KB 时 returncode=2 (实际={rc})")
    must("找不到" in err or "未启用" in err or "kb_path" in err,
         f"应给出明确错误提示 (err={err.strip()[:80]})")


def test_repo_root_detected_from_subdir(tmp_path: Path):
    print("\n═══ [F] P0-1：子目录 --repo . 应解析回 git 根，不误索引子目录假 KB ═══")
    # mock git 根 + 主 KB（5 个 md）
    (tmp_path / ".git").mkdir()
    build_minimal_kb(tmp_path)
    # 子目录 + 假 KB 诱饵（md 数不同，模拟本项目 control_plane/docs/kb 场景）
    sub = tmp_path / "control_plane"
    fake = sub / "docs" / "kb"
    fake.mkdir(parents=True)
    (fake / "a.md").write_text("# fake kb\n", encoding="utf-8")

    # 子目录里跑 --repo . → 应命中 git 根主 KB，而非子目录假 KB
    rc, out, _ = run_cwd(sub, "--backend", "project-note", "--json", "stats")
    must(rc == 0, f"子目录 --repo . stats 返回 0 (实际={rc})")
    data = json.loads(out)
    must(data["total_docs"] == 5,
         f"应索引 git 根主 KB=5，而非子目录假 KB=1 (实际={data['total_docs']})")


def test_repo_root_detected_from_subdir_explicit_dot(tmp_path: Path):
    print("\n═══ [F] P0-1：子目录显式 --repo . 同样纠偏 ═══")
    (tmp_path / ".git").mkdir()
    build_minimal_kb(tmp_path)
    sub = tmp_path / "control_plane"
    fake = sub / "docs" / "kb"
    fake.mkdir(parents=True)
    (fake / "a.md").write_text("# fake kb\n", encoding="utf-8")

    # 显式 --repo .（cwd 已在子目录）→ 同样向上探测
    rc, out, _ = run_cwd(sub, "--repo", ".", "--backend", "project-note", "--json", "stats")
    must(rc == 0, f"子目录 --repo . stats 返回 0 (实际={rc})")
    data = json.loads(out)
    must(data["total_docs"] == 5,
         f"显式 --repo . 也应纠偏到 git 根主 KB=5 (实际={data['total_docs']})")


def test_missing_kb_json_structured(tmp_path: Path):
    print("\n═══ [F] P0-2：--json 缺 KB 应输出结构化 unavailable，而非空 hits ═══")
    rc, out, _ = run(tmp_path, "--backend", "project-note", "--json", "search", "anything")
    must(rc == 2, f"缺 KB --json 应 rc=2 (实际={rc})")
    data = json.loads(out)
    must(data.get("unavailable") is True, f"unavailable=True (实际={data.get('unavailable')})")
    must("unavailable_reason" in data, "含 unavailable_reason 字段")
    must("kb_path" in data["unavailable_reason"] or "KB" in data["unavailable_reason"],
         f"原因应可操作 (实际={data['unavailable_reason']})")


def test_kb_dir_empty_unavailable(tmp_path: Path):
    print("\n═══ [F] P0-2：kb_path 指向空目录应 unavailable，而非静默 0 命中 ═══")
    empty_kb = tmp_path / "docs" / "empty-kb"
    empty_kb.mkdir(parents=True)
    # 同时写 yml + json（_load_yml 的 json fallback 需 yml 存在才生效，双份保证环境无关）
    (tmp_path / ".query-knowledge.yml").write_text(
        "backends:\n  project-note:\n    kb_path: docs/empty-kb\n",
        encoding="utf-8",
    )
    (tmp_path / ".query-knowledge.json").write_text(
        json.dumps({"backends": {"project-note": {"kb_path": "docs/empty-kb"}}}),
        encoding="utf-8",
    )
    rc, out, _ = run(tmp_path, "--backend", "project-note", "--json", "search", "x")
    must(rc == 2, f"空 KB 目录 --json 应 rc=2 (实际={rc})")
    data = json.loads(out)
    must(data.get("unavailable") is True, "unavailable=True")
    must("空" in data["unavailable_reason"], f"原因提示目录为空 (实际={data['unavailable_reason']})")


def test_real_zero_hits_still_empty(tmp_path: Path):
    print("\n═══ [F] P0-2：真实无匹配仍返回 hit_count:0（三态 C 与 unavailable 可区分）═══")
    build_minimal_kb(tmp_path)
    rc, out, _ = run(tmp_path, "--backend", "project-note", "--json", "search", "绝不存在的关键词zzzz")
    must(rc == 0, f"KB 正常但无匹配应 rc=0 (实际={rc})")
    data = json.loads(out)
    must("unavailable" not in data, "不应含 unavailable 字段")
    must(data["hit_count"] == 0, f"hit_count=0 (实际={data['hit_count']})")


# ────────────────────────── [G] kb-aware 子命令（D2/T3）──────────────────────────

def test_kb_aware_basic(full_repo: Path):
    print("\n═══ [G1] kb-aware 首次调用返回 overview+search ═══")
    rc, out, _ = run(full_repo, "--backend", "project-note", "--json",
                     "kb-aware", "--task-keywords", "架构 分层")
    must(rc == 0, f"kb-aware returncode=0 (实际={rc})")
    d = json.loads(out)
    must("overview" in d and "search" in d, "返回含 overview 和 search")
    must(d["overview"]["cached"] is False, "首次 overview.cached=False")
    must(d["search"]["cached"] is False, "首次 search.cached=False")
    must(d["overview"]["kb_source"] in ("auto-detect", "config-file"),
         f"kb_source 正常 (实际={d['overview']['kb_source']})")
    must("cache_path" in d and "kb_content_hash" in d, "返回含 cache_path 和 kb_content_hash")


def test_kb_aware_cache_hit(full_repo: Path):
    print("\n═══ [G2] kb-aware 同参数二次调用应命中缓存 ═══")
    args = ("--backend", "project-note", "--json", "kb-aware", "--task-keywords", "架构 分层")
    # 第一次
    run(full_repo, *args)
    # 第二次同参数
    rc, out, _ = run(full_repo, *args)
    must(rc == 0, f"二次调用 returncode=0 (实际={rc})")
    d = json.loads(out)
    must(d["overview"]["cached"] is True, "二次 overview.cached=True")
    must(d["search"]["cached"] is True, "二次 search.cached=True")


def test_kb_aware_different_keywords(full_repo: Path):
    print("\n═══ [G3] kb-aware 不同关键词 search 不命中、overview 仍命中 ═══")
    run(full_repo, "--backend", "project-note", "--json", "kb-aware", "--task-keywords", "架构")
    rc, out, _ = run(full_repo, "--backend", "project-note", "--json",
                     "kb-aware", "--task-keywords", "状态管理 数据")
    d = json.loads(out)
    must(d["overview"]["cached"] is True, "overview 仍 cached=True（KB 未变）")
    must(d["search"]["cached"] is False, "search cached=False（关键词不同）")


def test_kb_aware_task_id_isolation(full_repo: Path):
    print("\n═══ [G4] kb-aware 不同 task_id 缓存隔离 ═══")
    run(full_repo, "--backend", "project-note", "--json",
        "kb-aware", "--task-keywords", "架构", "--task-id", "TASK-A")
    rc, out, _ = run(full_repo, "--backend", "project-note", "--json",
                     "kb-aware", "--task-keywords", "架构", "--task-id", "TASK-B")
    d = json.loads(out)
    must(d["overview"]["cached"] is False, "不同 task_id 的 overview 不命中（隔离）")
    must("TASK-B" in d["cache_path"], f"cache_path 含 TASK-B (实际={d['cache_path']})")


def test_kb_aware_path_traversal_blocked(full_repo: Path):
    print("\n═══ [G5] kb-aware task_id 路径穿越被拒 ═══")
    # 构造恶意 task_id
    rc, out, err = run(full_repo, "--backend", "project-note", "--json",
                       "kb-aware", "--task-keywords", "x", "--task-id", "../../../tmp/hacked")
    must(rc != 0, f"恶意 task_id 应非零退出 (实际={rc})")
    must("路径穿越" in err or "task_id" in err or "ValueError" in err,
         f"应提示路径穿越/校验失败 (err={err.strip()[:100]})")


def test_kb_aware_empty_keywords(full_repo: Path):
    print("\n═══ [G6] kb-aware 空关键词只返回 overview ═══")
    rc, out, _ = run(full_repo, "--backend", "project-note", "--json", "kb-aware")
    must(rc == 0, f"空关键词 returncode=0 (实际={rc})")
    d = json.loads(out)
    must(d["search"]["hit_count"] == 0, "空关键词 hit_count=0")
    must("note" in d["search"], "空关键词有 note 说明")


def test_kb_aware_task_id_with_dot_accepted(full_repo: Path):
    print("\n═══ [G7] kb-aware 合法含点号 task_id（B1 回归保护）═══")
    # 对齐 control-plane _SLUG_RE 实际生成的格式（含 . 用于版本号）
    rc, out, _ = run(full_repo, "--backend", "project-note", "--json",
                     "kb-aware", "--task-keywords", "架构",
                     "--task-id", "TASK-20260712-190508-fix-v1.2.3")
    must(rc == 0, f"含点号 task_id 应成功 (实际={rc})")
    d = json.loads(out)
    must("fix-v1.2.3" in d["cache_path"], f"cache_path 含 task_id (实际={d['cache_path']})")


def test_kb_aware_long_task_id_rejected(full_repo: Path):
    print("\n═══ [G8] kb-aware 超长 task_id 被拒（B2 回归保护）═══")
    # 101 字符应被拒（边界值）
    long_id = "a" * 101
    rc, out, err = run(full_repo, "--backend", "project-note", "--json",
                       "kb-aware", "--task-keywords", "x",
                       "--task-id", long_id)
    must(rc != 0, f"超长 task_id 应非零退出 (实际={rc})")
    must("长度超过" in err or "task_id" in err,
         f"应提示长度超限 (err={err.strip()[:100]})")
    # 100 字符应接受（边界值另一端）
    boundary_id = "a" * 100
    rc2, _, _ = run(full_repo, "--backend", "project-note", "--json",
                    "kb-aware", "--task-keywords", "x",
                    "--task-id", boundary_id)
    must(rc2 == 0, f"100 字符边界值应成功 (实际={rc2})")


# ────────────────────────── pytest 入口 ──────────────────────────

# 兼容直接 python3 运行（无 pytest 时）
def _run_all_tests():
    """不依赖 pytest，用 tmp_path 手动跑所有测试"""
    import tempfile
    tests = [
        ("test_backend_project_note_basic", test_backend_project_note_basic),
        ("test_backend_project_note_search", test_backend_project_note_search),
        ("test_backend_project_note_l1_protection", test_backend_project_note_l1_protection),
        ("test_backend_codegraph_stats", test_backend_codegraph_stats),
        ("test_backend_codegraph_search", test_backend_codegraph_search),
        ("test_backend_codegraph_callers", test_backend_codegraph_callers),
        ("test_backend_codegraph_missing_symbol", test_backend_codegraph_missing_symbol),
        ("test_backend_codegraph_schema_too_new", test_backend_codegraph_schema_too_new),
        ("test_backend_codegraph_schema_too_old", test_backend_codegraph_schema_too_old),
        ("test_backend_predev_scan", test_backend_predev_scan),
        ("test_backend_predev_empty_scope", test_backend_predev_empty_scope),
        ("test_backend_predev_artifacts_scan", test_backend_predev_artifacts_scan),
        ("test_backend_all_aggregated", test_backend_all_aggregated),
        ("test_backend_all_graceful_degradation", test_backend_all_graceful_degradation),
        ("test_config_file_override", test_config_file_override),
        ("test_config_auto_detect", test_config_auto_detect),
        ("test_json_parseable_all_backends", test_json_parseable_all_backends),
        ("test_missing_kb_graceful", test_missing_kb_graceful),
        ("test_repo_root_detected_from_subdir", test_repo_root_detected_from_subdir),
        ("test_repo_root_detected_from_subdir_explicit_dot", test_repo_root_detected_from_subdir_explicit_dot),
        ("test_missing_kb_json_structured", test_missing_kb_json_structured),
        ("test_kb_dir_empty_unavailable", test_kb_dir_empty_unavailable),
        ("test_real_zero_hits_still_empty", test_real_zero_hits_still_empty),
        ("test_kb_aware_basic", test_kb_aware_basic),
        ("test_kb_aware_cache_hit", test_kb_aware_cache_hit),
        ("test_kb_aware_different_keywords", test_kb_aware_different_keywords),
        ("test_kb_aware_task_id_isolation", test_kb_aware_task_id_isolation),
        ("test_kb_aware_path_traversal_blocked", test_kb_aware_path_traversal_blocked),
        ("test_kb_aware_empty_keywords", test_kb_aware_empty_keywords),
        ("test_kb_aware_task_id_with_dot_accepted", test_kb_aware_task_id_with_dot_accepted),
        ("test_kb_aware_long_task_id_rejected", test_kb_aware_long_task_id_rejected),
    ]
    print("🧪 query_knowledge.py fixture-based 测试")
    print(f"   SCRIPT: {SCRIPT}")
    print()

    for name, fn in tests:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            if "full_repo" in fn.__code__.co_varnames:
                build_full_repo(tmp)
                fn(tmp)
            elif "tmp_path" in fn.__code__.co_varnames:
                fn(tmp)

    print("\n🎉 全部测试通过！query_knowledge.py fixture-based 全可用")


if __name__ == "__main__":
    # 优先用 pytest 跑（如果安装了）
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
    except ImportError:
        _run_all_tests()