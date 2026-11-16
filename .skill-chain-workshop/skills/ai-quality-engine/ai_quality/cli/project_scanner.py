"""
backend/cli/project_scanner.py

ai-quality diagnose 全项目扫描器

对应数据流节点：B（CLI Adapter）
设计文档：v0.4 设计文档 §6

职责：
    discover(path, extra_ignore) -> (artifacts, warnings)
    递归发现目录下所有白名单代码文件，构造 {type:"code", path, content} artifact 列表。

fail-closed：
    - path 不是目录 / 扫描后 0 个代码文件 -> ValueError（由调用方转为错误输出）
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Set, Tuple

# 内置默认 ignore 目录（命中任一段即跳过）
DEFAULT_IGNORE_DIRS: Set[str] = {
    "node_modules",
    ".git",
    "__pycache__",
    "dist",
    "build",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "coverage",
    ".next",
    ".nuxt",
    "target",
    "vendor",
}

# 扩展名白名单（首版语言范围：Python + JS/TS）
CODE_EXTENSIONS: Set[str] = {".py", ".js", ".jsx", ".ts", ".tsx"}


@dataclass
class BudgetConfig:
    """扫描预算：单文件行数 / 项目文件数 / 项目总大小。"""

    max_file_lines: int = 2000
    max_project_files: int = 500
    max_project_size_mb: int = 10


def _load_gitignore(root: Path) -> List[str]:
    """读取项目 .gitignore，返回非空、非注释行（简易 glob 模式，不引入 pathspec）。"""
    patterns: List[str] = []
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return patterns
    try:
        text = gitignore.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return patterns
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _matches_any(relative: Path, patterns: List[str]) -> bool:
    """简易 glob 匹配：无斜杠 pattern 命中相对路径任一段；含斜杠命中整体相对路径。"""
    if not patterns:
        return False
    rel = relative.as_posix()
    parts = set(relative.parts)
    for pattern in patterns:
        pat = pattern.rstrip("/")
        if not pat:
            continue
        if "/" in pat:
            if rel == pat or rel.startswith(pat + "/"):
                return True
        else:
            if pat in parts:
                return True
    return False


def discover(path: str, extra_ignore: Tuple[str, ...] = ()) -> Tuple[List[dict], List[str]]:
    """递归扫描 path 目录，返回 (artifacts, warnings)。"""
    root = Path(path)
    if not root.exists():
        raise ValueError(f"Path not found: {path}")
    if not root.is_dir():
        raise ValueError(f"Not a directory: {path}")

    budget = BudgetConfig()
    ignore_patterns = _load_gitignore(root)
    extra_patterns = list(extra_ignore)

    artifacts: List[dict] = []
    warnings: List[str] = []
    scanned_size_bytes = 0

    for candidate in root.rglob("*"):
        # 跳过符号链接（避免循环）与非白名单扩展名
        if candidate.is_symlink() or candidate.is_dir():
            continue
        if candidate.suffix.lower() not in CODE_EXTENSIONS:
            continue
        try:
            rel = candidate.relative_to(root)
        except ValueError:
            continue
        # ignore 过滤：内置目录名 / .gitignore / 用户追加
        rel_lower_parts = {p.lower() for p in rel.parts}
        if rel_lower_parts & DEFAULT_IGNORE_DIRS:
            continue
        if _matches_any(rel, ignore_patterns) or _matches_any(rel, extra_patterns):
            continue
        # 预算：项目文件数
        if len(artifacts) >= budget.max_project_files:
            warnings.append(
                f"Project file budget exceeded ({budget.max_project_files} files), skipping: {candidate}"
            )
            continue
        try:
            stat = candidate.stat()
        except OSError:
            continue
        # 预算：项目总大小
        scanned_size_bytes += stat.st_size
        if scanned_size_bytes > budget.max_project_size_mb * 1024 * 1024:
            warnings.append(
                f"Project size budget exceeded ({budget.max_project_size_mb}MB), skipping: {candidate}"
            )
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # 预算：单文件行数
        if content.count("\n") + 1 > budget.max_file_lines:
            warnings.append(
                f"File line budget exceeded ({budget.max_file_lines} lines), skipping: {candidate}"
            )
            continue
        artifacts.append(
            {
                "type": "code",
                "path": str(candidate),
                "content": content,
            }
        )

    if not artifacts:
        raise ValueError(f"No code files found under: {path}")

    return artifacts, warnings
