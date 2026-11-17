"""
conftest.py — pytest fixtures for query_knowledge tests

提供两个核心 fixture：
  - minimal_kb_repo: 在 tmp_path 里 mock 一个最小 KB（docs/project-note/ 三层结构）
  - minimal_codegraph_repo: 在 tmp_path 里 mock 一个最小 codegraph.db

测试不依赖任何真实项目，可在 CI 里跑。
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path


def _write_kb(repo_root: Path) -> Path:
    """在 repo_root 下建一个最小 docs/project-note/ KB"""
    kb = repo_root / "docs" / "project-note"
    kb.mkdir(parents=True, exist_ok=True)

    # L1: 根 README
    (kb / "README.md").write_text(textwrap.dedent("""\
        # Test Project KB

        > 总索引

        ## 6 大分类概览

        | # | 分类 |
        |---|------|
        | 01 | [架构](./01-架构/) |
        | 02 | [功能](./02-功能/) |

        ## 关键事实

        - **架构**: 三层架构（前端 + 后端 + 数据层）
        - **核心入口**: app.py
        """), encoding="utf-8")

    # L2: 01-架构 README
    (kb / "01-架构").mkdir(parents=True, exist_ok=True)
    (kb / "01-架构" / "README.md").write_text(textwrap.dedent("""\
        # 01-架构

        > 系统架构文档

        ## 文档清单

        | 文档 | 内容 |
        |------|------|
        | [分层架构.md](./分层架构.md) | 前后端分层 |
        """), encoding="utf-8")

    # L3: 01-架构/分层架构.md
    (kb / "01-架构" / "分层架构.md").write_text(textwrap.dedent("""\
        # 分层架构

        > 最后更新：2026-07-05

        ## 三层结构

        - **前端**：vanilla JS
        - **后端**：Python http.server
        - **数据层**：SQLite

        ## 关键事实

        - **核心入口**：app.py
        - **端口**：8765

        ## 主题切换

        主题切换通过 applyTheme 函数实现。
        """), encoding="utf-8")

    # L3: 02-功能/README.md（L2）+ 一个 L3
    (kb / "02-功能").mkdir(parents=True, exist_ok=True)
    (kb / "02-功能" / "README.md").write_text("# 02-功能\n\n> 功能文档\n", encoding="utf-8")
    (kb / "02-功能" / "多主题切换.md").write_text(textwrap.dedent("""\
        # 多主题切换

        > 最后更新：2026-07-05

        ## 触发点

        - 点击 #theme-toggle 按钮

        ## 实现

        applyTheme(themeName) 函数实现主题切换。
        """), encoding="utf-8")

    return kb


def _write_codegraph(repo_root: Path) -> Path:
    """在 repo_root/.codegraph/ 下建一个最小 codegraph.db"""
    import sqlite3
    cg_dir = repo_root / ".codegraph"
    cg_dir.mkdir(parents=True, exist_ok=True)
    db_path = cg_dir / "codegraph.db"

    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 建表（简化版，只建必要字段）
    cur.executescript("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            kind TEXT,
            name TEXT,
            qualified_name TEXT,
            file_path TEXT,
            start_line INTEGER,
            end_line INTEGER,
            start_column INTEGER,
            end_column INTEGER,
            docstring TEXT,
            signature TEXT,
            visibility TEXT,
            is_exported INTEGER,
            is_async INTEGER,
            is_static INTEGER,
            is_abstract INTEGER,
            decorators TEXT,
            type_parameters TEXT,
            updated_at INTEGER
        );

        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source TEXT,
            target TEXT,
            kind TEXT,
            metadata TEXT,
            line INTEGER,
            col INTEGER,
            provenance TEXT
        );

        CREATE TABLE files (
            path TEXT PRIMARY KEY,
            content_hash TEXT,
            language TEXT,
            size INTEGER,
            modified_at INTEGER,
            indexed_at INTEGER,
            node_count INTEGER,
            errors TEXT
        );

        CREATE TABLE project_metadata (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at INTEGER
        );

        CREATE TABLE schema_versions (
            version INTEGER,
            applied_at INTEGER,
            description TEXT
        );

        -- FTS5 external content 表（与真实 codegraph schema 对齐）
        CREATE VIRTUAL TABLE nodes_fts USING fts5(
            name, qualified_name, docstring, signature,
            content='nodes', content_rowid='rowid'
        );
    """)

    # 插入测试数据
    cur.executescript("""
        INSERT INTO schema_versions (version, applied_at, description)
        VALUES (4, 0, 'initial schema includes all migrations');

        INSERT INTO nodes (id, kind, name, qualified_name, file_path, start_line, signature)
        VALUES
            ('function:abc', 'function', 'Handler', 'Handler.do_GET', 'monitor/server.py', 100, '(self)'),
            ('function:def', 'function', '_memory_auth', '_memory_auth', 'monitor/server.py', 200, '(handler)'),
            ('class:ghi', 'class', 'App', 'App', 'app.py', 1, '()');

        -- FTS5 external content 表：直接 INSERT 会自动同步 rowid
        INSERT INTO nodes_fts (rowid, name, qualified_name, docstring, signature)
        SELECT rowid, name, qualified_name, docstring, signature FROM nodes;

        INSERT INTO edges (source, target, kind, line) VALUES
            ('Handler.do_GET', '_memory_auth', 'call', 105),
            ('App', 'Handler.do_GET', 'call', 10);

        INSERT INTO files (path, language, size, node_count) VALUES
            ('monitor/server.py', 'python', 1000, 2),
            ('app.py', 'python', 100, 1);
    """)

    conn.commit()
    conn.close()
    return db_path


def _write_predev_artifacts(repo_root: Path) -> Path:
    """在 .claude/artifacts/bugs/xxx/knowledge/ 下建一个最小知识文件"""
    artifacts = repo_root / ".claude" / "artifacts" / "bugs" / "test-bug-001" / "knowledge"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "lesson.md").write_text(textwrap.dedent("""\
        # Bug 教训：SSE 流式数据未清理

        ## 检测规则
        - grep "addEventListener" 检查是否清理

        ## 修复模板
        ```js
        eventSource.close();
        ```
        """), encoding="utf-8")
    return artifacts


# ── pytest fixtures ──

import pytest


@pytest.fixture
def minimal_kb_repo(tmp_path: Path) -> Path:
    """提供一个只含 KB 的临时 repo"""
    _write_kb(tmp_path)
    return tmp_path


@pytest.fixture
def minimal_codegraph_repo(tmp_path: Path) -> Path:
    """提供一个只含 codegraph.db 的临时 repo"""
    _write_codegraph(tmp_path)
    return tmp_path


@pytest.fixture
def full_repo(tmp_path: Path) -> Path:
    """提供一个含 KB + codegraph + predev artifacts 的完整 mock repo"""
    _write_kb(tmp_path)
    _write_codegraph(tmp_path)
    _write_predev_artifacts(tmp_path)
    return tmp_path


# ── 也可作为普通函数导入（兼容非 pytest 调用）──

def build_minimal_kb(repo_root: Path) -> Path:
    return _write_kb(repo_root)


def build_minimal_codegraph(repo_root: Path) -> Path:
    return _write_codegraph(repo_root)


def build_predev_artifacts(repo_root: Path) -> Path:
    return _write_predev_artifacts(repo_root)


def build_full_repo(repo_root: Path) -> Path:
    _write_kb(repo_root)
    _write_codegraph(repo_root)
    _write_predev_artifacts(repo_root)
    return repo_root