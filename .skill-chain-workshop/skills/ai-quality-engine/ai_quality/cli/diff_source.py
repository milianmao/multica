"""
backend/cli/diff_source.py

ai-quality check --diff 的 git diff 摄入源

读取当前 git 仓库的未暂存 + 已暂存代码文件变更，将每个变更文件构造成
{type:"code", path, content} artifact，交给 EvaluationCore 统一评测。

对应数据流节点：B（CLI Adapter，diff 摄入分支）

准入（fail-closed，REQ-ADAPTER-01）：
- 非 git 仓库 / 无代码文件变更 -> 抛 RuntimeError，由入口转非零退出码 + 明确错误
- 路径锚定：git 返回仓库根相对路径，必须用 `git rev-parse --show-toplevel`
  锚定根后 root.joinpath()，避免在子目录运行时路径错位静默读错文件
"""

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

# 代码文件扩展名白名单（与规则引擎支持的 JS/TS 双语言 + Python 对齐，不含 .env/密钥）
CODE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py"}
DEFAULT_ENCODING = "utf-8"


class GitDiffError(RuntimeError):
    """git diff 摄入失败（非 git / 无变更 / git 命令异常），fail-closed。"""


def _run_git(args: List[str], cwd: Path) -> str:
    """安全执行 git 命令，失败抛 GitDiffError（fail-closed）。"""
    if shutil.which("git") is None:
        raise GitDiffError("未检测到 git 命令（git 不在 PATH 中），check --diff 无法工作")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
    except OSError as e:  # 非预期执行失败也 fail-closed
        raise GitDiffError(f"git 执行失败: {e}") from e
    if proc.returncode != 0:
        raise GitDiffError(f"git {args[0]} 失败: {proc.stderr.strip()}")
    return proc.stdout


def _is_git_repo(cwd: Path) -> bool:
    """判断 cwd 是否位于 git 仓库内。"""
    try:
        # rev-parse --is-inside-work-tree 返回 true/false，非零仍说明非工作树
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _anchor_root(cwd: Path) -> Path:
    """获取 git 仓库根目录绝对路径（路径锚定，修复 subdir 错位）。"""
    out = _run_git(["rev-parse", "--show-toplevel"], cwd)
    return Path(out.strip())


def _changed_code_files(cwd: Path) -> List[str]:
    """
    收集未暂存 + 已暂存、且扩展名在白名单内的变更文件（仓库根相对路径）。

    - HEAD 不存在时（首 commit 前全部 untracked），git diff --name-only 返回空，
      此处按无变更处理并 fail-closed（设计 §6.3：untracked 需 git add 或 check <path>）。
    """
    out = _run_git(["diff", "--name-only"], cwd)
    out += "\n" + _run_git(["diff", "--cached", "--name-only"], cwd)  # 已暂存
    seen: set = set()
    files: List[str] = []
    for line in out.splitlines():
        rel = line.strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        ext = Path(rel).suffix.lower()
        if ext in CODE_EXTENSIONS:
            files.append(rel)
    return files


def build_from_git_diff(cwd: Optional[Path] = None) -> List[dict]:
    """
    从当前 git 改动构造待评测的 artifact 列表。

    返回 List[artifact dict]，形如：
        [{"type": "code", "path": "<绝对路径>", "content": "<当前工作树内容>"}]

    fail-closed：非 git / 无代码变更 / git 异常 -> 抛 GitDiffError。
    变更的已删除文件会被跳过（工作树无内容可读）。
    """
    cwd = Path(cwd) if cwd is not None else Path.cwd()

    if not _is_git_repo(cwd):
        raise GitDiffError("当前目录不在 git 仓库内，check --diff 需要 git 仓库上下文")

    root = _anchor_root(cwd)  # 仓库根绝对路径，用于 joinpath 锚定
    changed = _changed_code_files(cwd)

    if not changed:
        raise GitDiffError(
            "未检测到代码文件变更（.ts/.tsx/.js/.jsx/.py）。"
            "提示：untracked 新文件不在 git diff 范围，需先 `git add` 或改用 `ai-quality check <path>`。"
        )

    artifacts: List[dict] = []
    for rel in changed:
        abs_path = root.joinpath(rel)
        if not abs_path.is_file():
            # 已删除 / 无法读取：跳过（无工作树内容可评测）
            continue
        try:
            content = abs_path.read_text(encoding=DEFAULT_ENCODING)
        except (OSError, UnicodeDecodeError):
            # 二进制/不可读：跳过该文件，保留其他变更
            continue
        artifacts.append(
            {
                "type": "code",
                "path": str(abs_path),
                "content": content,
            }
        )

    if not artifacts:
        raise GitDiffError("变更文件均可读内容为空（可能均为已删除或二进制文件），无 artifact 可评测")

    return artifacts
