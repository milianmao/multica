#!/usr/bin/env python3
"""
query_knowledge.py — 统一检索入口（项目知识背景）

设计原则：渐进式披露 + 多后端路由
- project-note 后端：检索 docs/project-note/ 三层 Markdown KB
- codegraph 后端：检索 .codegraph/ 知识图谱（符号级）
- predev 后端：触发 pre-dev-checklist skill（错题本匹配）
- all 后端：聚合以上所有（按相关度排序）

CLI 模式（向后兼容 SKILL.md 描述的 7 种模式）：
  overview     — 项目概览（默认走 L1 README）
  list         — 列出所有分类
  category     — 列出某分类下所有文档
  search       — 按关键词搜索（L3 文档全文）
  stats        — KB 统计
  read         — 读取指定文档全文
  hierarchy    — 打印三层结构
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable


# ──────────────────────────────────────────────
# 配置加载（auto-detect + .query-knowledge.yml）
# ──────────────────────────────────────────────

class QueryKnowledgeConfig:
    """项目级配置 — auto-detect + 可选 .query-knowledge.yml 覆盖

    配置发现优先级（高 → 低）：
      1. 项目根 .query-knowledge.yml（显式配置）
      2. auto-detect（扫常见路径）
      3. 内置默认值（最朴素 Markdown rglob）
    """

    DEFAULTS = {
        "backends": {
            "project-note": {
                "enabled": True,
                "kb_path": None,           # auto-detect
                "category_pattern": r"^\d{2}-",
                "layer_rules": {
                    "L1": "^(?!.*/)README\.md$",         # 根 README
                    "L2": ".*/README\.md$",              # 子目录 README
                    "L3": ".+/[^/]+\.md$",               # 其它 .md
                },
            },
            "codegraph": {
                "enabled": True,
                "db_path": ".codegraph/codegraph.db",
            },
            "predev": {
                "enabled": True,
                "artifacts_dir": ".claude/artifacts/bugs",
                "extra_rules": [],
            },
        },
    }

    # KB 常见路径（auto-detect 用）
    KB_CANDIDATE_PATHS = [
        "docs/project-note",
        "docs/kb",
        "docs/knowledge",
        "docs/architecture",
        "docs",
    ]

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.config = self._load()

    def _load(self) -> dict:
        """加载配置：.query-knowledge.yml > auto-detect > 默认值"""
        config = self._deep_copy_defaults()

        # 1) 尝试加载 yml
        yml_path = self.repo_root / ".query-knowledge.yml"
        yml_config = self._load_yml(yml_path)

        # 2) auto-detect KB 路径（如果 yml 没显式指定）
        kb_path = (
            yml_config.get("backends", {})
            .get("project-note", {})
            .get("kb_path")
        )
        if not kb_path:
            kb_path = self._auto_detect_kb()
        config["backends"]["project-note"]["kb_path"] = kb_path

        # 3) 合并 yml 配置（深度合并）
        if yml_config:
            self._deep_merge(config, yml_config)

        return config

    def _load_yml(self, path: Path) -> dict:
        """加载 .query-knowledge.yml（纯 stdlib，不依赖 PyYAML）

        支持的简化语法（不是完整 YAML，但够用）：
            backends:
              project-note:
                kb_path: docs/kb
              predev:
                extra_rules:
                  - rule: My Custom Rule
                    keywords: [foo, bar]
        """
        if not path.exists():
            return {}
        try:
            import yaml  # type: ignore
            with path.open(encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            # 没 PyYAML 时尝试用 json 兜底（用户可写 .query-knowledge.json）
            json_path = path.with_suffix(".json")
            if json_path.exists():
                import json as _json
                with json_path.open(encoding="utf-8") as f:
                    return _json.load(f)
            return {}

    def _auto_detect_kb(self) -> str | None:
        """扫 KB_CANDIDATE_PATHS，找到含 .md 文件的第一个"""
        for candidate in self.KB_CANDIDATE_PATHS:
            path = self.repo_root / candidate
            if path.is_dir():
                # 至少有 1 个 .md 文件才算 KB
                if any(path.rglob("*.md")):
                    return candidate
        return None

    def _deep_copy_defaults(self) -> dict:
        import copy
        return copy.deepcopy(self.DEFAULTS)

    def _deep_merge(self, base: dict, override: dict) -> None:
        """递归合并 override 到 base（in-place）"""
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v

    # ── 访问器 ──

    def kb_path(self) -> Path | None:
        p = self.config["backends"]["project-note"]["kb_path"]
        return self.repo_root / p if p else None

    def kb_category_pattern(self) -> str:
        return self.config["backends"]["project-note"]["category_pattern"]

    def kb_layer_rules(self) -> dict:
        return self.config["backends"]["project-note"]["layer_rules"]

    def codegraph_db_path(self) -> Path:
        p = self.config["backends"]["codegraph"]["db_path"]
        return self.repo_root / p

    def predev_artifacts_dir(self) -> Path:
        p = self.config["backends"]["predev"]["artifacts_dir"]
        return self.repo_root / p

    def predev_extra_rules(self) -> list:
        return self.config["backends"]["predev"]["extra_rules"]

    def is_backend_enabled(self, name: str) -> bool:
        return self.config["backends"].get(name, {}).get("enabled", False)


# ──────────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────────

@dataclass
class KbDoc:
    """单个 KB 文档的元信息"""
    path: str            # 相对 repo 根的路径
    category: str        # 01-架构总览 / 02-功能结构和策略 / ...
    layer: str           # L1 / L2 / L3
    title: str           # 从 # 标题提取
    summary: str = ""    # 从 "关键事实" 段或第一段提取
    line_count: int = 0
    headings: list[str] = field(default_factory=list)  # 所有 ## / ### 标题


@dataclass
class SearchHit:
    """一次检索的单条命中"""
    doc: KbDoc
    score: float         # 相关度 0~1
    matched_keywords: list[str] = field(default_factory=list)
    matched_sections: list[str] = field(default_factory=list)  # 命中的 ## 章节
    snippet: str = ""    # 第一个匹配点的上下文


# ──────────────────────────────────────────────
# Backend Protocol — 统一抽象 + 注册表
# ──────────────────────────────────────────────

try:
    from typing import Protocol, runtime_checkable
except ImportError:
    # Python 3.7 兼容
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


@runtime_checkable
class Backend(Protocol):
    """所有检索后端的统一接口

    实现类需提供：
      - name: 后端标识（project-note / codegraph / predev）
      - is_available(): 数据源是否可用（不存在时优雅降级）
      - search(query, **kwargs): 主检索接口
      - stats(): 数据源统计
    """
    name: str

    def is_available(self) -> bool: ...
    def search(self, query: str, **kwargs) -> list[dict]: ...
    def stats(self) -> dict: ...


# 后端注册表（main() 用这个动态发现可用后端）
# 延迟填充，避免循环引用 — 在文件末尾填
BACKEND_REGISTRY: list[type] = []


# ──────────────────────────────────────────────
# 索引构建（只构建一次，后续检索复用）
# ──────────────────────────────────────────────

class KbIndex:
    """项目知识库索引（针对 docs/<kb-path>/ 三层结构）"""

    name = "project-note"

    LAYER_RULES = [
        ("L1", re.compile(r"^README\.md$")),                  # 根 README
        ("L2", re.compile(r"/README\.md$")),                  # 分类下 README
        ("L3", re.compile(r"/[^/]+\.md$")),                   # 其它 .md
    ]
    HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
    SUMMARY_RE = re.compile(
        r"##\s*关键事实.*?\n(.*?)(?=\n##|\Z)",
        re.DOTALL,
    )

    def __init__(self, repo_root: Path, config: "QueryKnowledgeConfig | None" = None):
        self.repo_root = repo_root
        self.config = config or QueryKnowledgeConfig(repo_root)
        self.kb_root = self.config.kb_path()
        self.category_pattern = re.compile(self.config.kb_category_pattern())
        self.docs: list[KbDoc] = []
        self.by_layer: dict[str, list[KbDoc]] = {"L1": [], "L2": [], "L3": []}
        self._build()

    def _classify_layer(self, rel_path: str) -> str:
        """三层分类：L1 = 根 README，L2 = 子目录 README，L3 = 其它

        适配任意 KB 路径（不再硬编码 'project-note'）：
          - 计算 rel_path 相对 kb_root 的部分
          - 用路径段数判断层级
        """
        if not self.kb_root:
            return "L3"
        try:
            rel_to_kb = Path(rel_path).relative_to(self.kb_root.relative_to(self.repo_root))
            parts = rel_to_kb.parts
        except ValueError:
            parts = Path(rel_path).parts
        if not parts:
            return "L1"
        # 根 README
        if len(parts) == 1 and parts[0] == "README.md":
            return "L1"
        # 子目录 README
        if len(parts) == 2 and parts[1] == "README.md":
            return "L2"
        return "L3"

    def _extract_category(self, rel_path: str) -> str:
        """从路径中识别分类：用配置的 category_pattern 匹配路径段

        默认 pattern: ^\\d{2}- （匹配 '01-架构总览' 这种）
        用户可在 .query-knowledge.yml 自定义 pattern 适配本项目命名约定。
        """
        parts = Path(rel_path).parts
        for p in parts:
            if self.category_pattern.match(p):
                return p
        # 兜底：取 kb_root 下第一级目录名
        if self.kb_root:
            try:
                rel_to_kb = Path(rel_path).relative_to(self.kb_root.relative_to(self.repo_root))
                if rel_to_kb.parts:
                    return rel_to_kb.parts[0]
            except ValueError:
                pass
        return "(root)"

    def _build(self) -> None:
        if not self.kb_root.exists():
            return
        for md in sorted(self.kb_root.rglob("*.md")):
            rel = str(md.relative_to(self.repo_root))
            text = md.read_text(encoding="utf-8", errors="ignore")
            layer = self._classify_layer(rel)
            category = self._extract_category(rel)

            # 提取首个 # 标题
            m = re.search(r"^#\s+(.+?)$", text, re.MULTILINE)
            title = m.group(1).strip() if m else md.stem

            # 提取关键事实段作为 summary
            sm = self.SUMMARY_RE.search(text)
            summary = ""
            if sm:
                # 取第一行 bullet
                first_bullet = re.search(r"[-*]\s+\*\*(.+?)\*\*[：:]\s*(.+)", sm.group(1))
                if first_bullet:
                    summary = f"{first_bullet.group(1)}: {first_bullet.group(2)}"

            headings = self.HEADING_RE.findall(text)
            heading_texts = [h[1].strip() for h in headings]

            doc = KbDoc(
                path=rel,
                category=category,
                layer=layer,
                title=title,
                summary=summary,
                line_count=text.count("\n") + 1,
                headings=heading_texts,
            )
            self.docs.append(doc)
            self.by_layer[layer].append(doc)

    # ── 检索 API ──

    # 错题本加权：根据 --context 决定是否给 06-迭代与变更/已知问题与陷阱.md 提权
    PITFALL_BOOST_DOCS = ("已知问题与陷阱.md",)
    PITFALL_BOOST_FACTOR = 2.0

    def is_available(self) -> bool:
        """KB 是否可用（至少有 1 个 .md 文件）"""
        return bool(self.kb_root and self.kb_root.exists() and self.docs)

    def stats(self) -> dict:
        """KB 统计"""
        by_cat: dict[str, int] = {}
        for d in self.docs:
            by_cat[d.category] = by_cat.get(d.category, 0) + 1
        return {
            "total_docs": len(self.docs),
            "total_lines": sum(d.line_count for d in self.docs),
            "by_layer": {l: len(self.by_layer.get(l, [])) for l in ["L1", "L2", "L3"]},
            "by_category": by_cat,
            "kb_root": str(self.kb_root.relative_to(self.repo_root)) if self.kb_root else None,
        }

    def _tokenize(self, query: str) -> list[str]:
        """三层分词：原 query + split + camelCase/snake_case 拆分"""
        tokens: list[str] = []
        # 1) 原 query 整体（保留语义完整性）
        if query.strip():
            tokens.append(query.strip())
        # 2) 按空白/中英文逗号 split
        for k in re.split(r"[\s,，]+", query):
            k = k.strip()
            if k and k not in tokens:
                tokens.append(k)
        # 3) camelCase / snake_case 拆分（处理 TaskService → Task / Service）
        for k in list(tokens):
            parts = re.findall(r"[A-Z][a-z]+|[A-Z]+(?=[A-Z][a-z])|[a-z]+|[A-Z]+", k)
            for p in parts:
                if p and p not in tokens:
                    tokens.append(p)
            # snake_case 拆分
            for p in k.split("_"):
                if p and p not in tokens:
                    tokens.append(p)
        return tokens

    def search(
        self,
        query: str,
        layer_filter: str | None = None,
        context: str = "feature",
        top: int | None = None,
    ) -> list[SearchHit]:
        """渐进式披露的核心：按 query 评分，返回相关文档

        Args:
            query: 查询词
            layer_filter: 仅检索指定层（L1/L2/L3）
            context: bugfix/feature — 决定是否给错题本加权
            top: 仅返回前 N 条；L1 README 在 top 模式下插入尾部
        """
        keywords = self._tokenize(query)
        candidates = self.docs
        if layer_filter:
            candidates = self.by_layer.get(layer_filter, [])

        hits: list[SearchHit] = []
        for doc in candidates:
            text_path = self.repo_root / doc.path
            text = text_path.read_text(encoding="utf-8", errors="ignore")

            matched_kw, matched_sec, snippet = self._score_doc(doc, text, keywords)
            if not matched_kw and not matched_sec:
                continue

            score = self._compute_relevance(doc, matched_kw, matched_sec, keywords)
            # bugfix 场景：错题本加权
            if context == "bugfix" and any(
                boost in doc.path for boost in self.PITFALL_BOOST_DOCS
            ):
                score = min(score * self.PITFALL_BOOST_FACTOR, 1.0)
            hits.append(SearchHit(
                doc=doc,
                score=score,
                matched_keywords=matched_kw,
                matched_sections=matched_sec,
                snippet=snippet,
            ))

        # 排序：L1 强制放尾部（top 模式） / 整体按 score 排
        hits.sort(
            key=lambda h: (h.doc.layer == "L1", -h.score)
        )
        if top:
            hits = hits[:top]
        return hits

    def _score_doc(
        self, doc: KbDoc, text: str, keywords: list[str]
    ) -> tuple[list[str], list[str], dict]:
        """渐进式评分：标题 > heading > summary > 全文

        Returns:
            matched_kw: 命中的关键词（去重保持顺序）
            matched_sec: 命中的 ## 章节标题（按出现顺序）
            snippet: {"section_title": str, "section_preview": str, "full_match": str}
                     两种 snippet 都返回，让 Agent 自己挑
        """
        matched_kw: list[str] = []
        matched_sec: list[str] = []

        # ── 1) 标题命中（强信号）──
        title_lower = doc.title.lower()
        for kw in keywords:
            if kw.lower() in title_lower and kw not in matched_kw:
                matched_kw.append(kw)

        # ── 2) headings 命中（中信号）+ 收集命中章节 ──
        for heading in doc.headings:
            heading_lower = heading.lower()
            for kw in keywords:
                if kw.lower() in heading_lower:
                    if kw not in matched_kw:
                        matched_kw.append(kw)
                    if heading not in matched_sec:
                        matched_sec.append(heading)
                    break  # 一个 heading 只要有一个 kw 命中就算命中

        # ── 3) summary 命中（中信号）──
        if doc.summary:
            summary_lower = doc.summary.lower()
            for kw in keywords:
                if kw.lower() in summary_lower and kw not in matched_kw:
                    matched_kw.append(kw)

        # ── 4) 全文命中（弱信号，L1 跳过全文匹配防膨胀）──
        section_title = ""
        section_preview = ""
        full_match = ""

        if doc.layer != "L1":
            text_lower = text.lower()
            for kw in keywords:
                if kw.lower() not in text_lower:
                    continue
                if kw not in matched_kw:
                    matched_kw.append(kw)
                # 找该 kw 第一次出现位置
                idx = text_lower.find(kw.lower())
                if idx == -1:
                    continue
                # 优先提取所在 ## 章节
                if not section_title:
                    section_title, section_preview = self._extract_section_context(
                        text, idx
                    )
                # 也保留 keyword ± 80 字符的精确片段
                if not full_match:
                    start = max(0, idx - 60)
                    end = min(len(text), idx + 120)
                    full_match = text[start:end].replace("\n", " ").strip()

        snippet = {
            "section_title": section_title,
            "section_preview": section_preview[:300] if section_preview else "",
            "full_match": full_match[:200] if full_match else "",
        }
        return matched_kw, matched_sec, snippet

    def _extract_section_context(
        self, text: str, match_idx: int
    ) -> tuple[str, str]:
        """找到 match_idx 所在 ## 章节，返回 (章节标题, 章节前 300 字符)"""
        # 找到 match_idx 之前最近的 ## 标题
        section_starts = [
            (m.start(), m.group(1).strip())
            for m in re.finditer(r"^##\s+(.+?)$", text, re.MULTILINE)
        ]
        section_title = ""
        section_start = 0
        for start, title in section_starts:
            if start <= match_idx:
                section_title = title
                section_start = start
            else:
                break
        # 截取该章节从标题到下一个 ## 之间
        next_starts = [s for s, _ in section_starts if s > section_start]
        section_end = next_starts[0] if next_starts else len(text)
        preview = text[section_start:section_end].strip()
        return section_title, preview

    def _compute_relevance(
        self,
        doc: KbDoc,
        matched_kw: list[str],
        matched_sec: list[str],
        keywords: list[str],
    ) -> float:
        """综合得分 0~1：位置加权 × 层权重 × 覆盖率

        位置权重（核心 IR 思想）：
          - 标题命中：1.0
          - heading 命中：0.7
          - summary 命中：0.5
          - 全文命中：0.3（最弱）
        """
        if not keywords:
            return 0.0

        layer_weight = {"L1": 0.3, "L2": 0.7, "L3": 1.0}[doc.layer]

        # 计算每个 keyword 的命中位置，取最高权重
        kw_max_pos_weight: dict[str, float] = {}
        title_lower = doc.title.lower()
        summary_lower = doc.summary.lower() if doc.summary else ""
        headings_lower = [h.lower() for h in doc.headings]

        for kw in keywords:
            w = 0.0
            if kw.lower() in title_lower:
                w = max(w, 1.0)
            if any(kw.lower() in h for h in headings_lower):
                w = max(w, 0.7)
            if kw.lower() in summary_lower:
                w = max(w, 0.5)
            # 全文命中（弱）
            if doc.layer != "L1":
                w = max(w, 0.3)
            kw_max_pos_weight[kw] = w

        # 综合得分 = Σ(pos_weight × layer_weight) / 总关键词数
        total = sum(kw_max_pos_weight.values()) * layer_weight
        return round(min(total / max(len(keywords), 1), 1.0), 3)


# ──────────────────────────────────────────────
# Codegraph 后端 — 符号级检索（SQLite 直读）
# ──────────────────────────────────────────────

class CodegraphIndex:
    """封装 .codegraph/codegraph.db，提供符号级查询

    4 种查询模式：
      search   — FTS5 全文搜索符号名/qualified_name/docstring/signature
      callers  — 谁调用了 X（impact: 修改 X 的爆炸半径）
      callees  — X 调用了什么（理解依赖）
      impact   — X 的全部影响（callers + 跨文件 impact）
    """

    name = "codegraph"
    # 本 Skill 已验证兼容的 codegraph schema 最高版本
    # v1→v4 是兼容演进（v4 = "Initial schema includes all migrations"，nodes/edges 字段不变）
    # 若 codegraph 升级到 v5+ 且破坏兼容，需更新此版本号 + 适配新字段
    SUPPORTED_SCHEMA_VERSION = 4
    # v1 仍可用（向后兼容），低于 v1 不支持
    MIN_SCHEMA_VERSION = 1

    def __init__(self, repo_root: Path, config: "QueryKnowledgeConfig | None" = None):
        self.repo_root = repo_root
        self.config = config or QueryKnowledgeConfig(repo_root)
        self.db_path = self.config.codegraph_db_path()
        self._schema_warned = False
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"codegraph DB 不存在: {self.db_path}（先跑 daemon 索引）"
            )

    def _conn(self):
        import sqlite3
        return sqlite3.connect(str(self.db_path))

    def is_available(self) -> bool:
        """DB 文件存在 + 必要表齐全 + schema 版本兼容

        不兼容时设置 self.unavailable_reason 供调用方读取。
        错误提示区分两种情况：
          - DB 版本 > SUPPORTED → 提醒用户更新 query-knowledge（不是重建 codegraph）
          - DB 版本 < MIN 或表缺失 → 提醒用户重建 codegraph 索引
        """
        self.unavailable_reason = None
        if not self.db_path.exists():
            self.unavailable_reason = f"DB 文件不存在: {self.db_path}（请先跑 codegraph daemon 建索引）"
            return False
        try:
            with self._conn() as conn:
                cur = conn.cursor()
                # 1) 检测必要的表是否存在
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('nodes','edges','nodes_fts','schema_versions')"
                )
                tables = {r[0] for r in cur.fetchall()}
                required = {"nodes", "edges", "nodes_fts"}
                if not required.issubset(tables):
                    missing = required - tables
                    self.unavailable_reason = (
                        f"DB 缺少必要表 {missing}（codegraph 索引不完整）。\n"
                        f"  → 请重建 codegraph 索引：`rm -rf .codegraph/ && 重启 daemon`"
                    )
                    return False
                # 2) 检测 schema 版本（区分"DB 太新"和"DB 太老"两种情况）
                if "schema_versions" in tables:
                    cur.execute("SELECT MAX(version) FROM schema_versions")
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        db_version = row[0]
                        if db_version > self.SUPPORTED_SCHEMA_VERSION:
                            # DB 比 query-knowledge 新 → 提醒更新 query-knowledge
                            self.unavailable_reason = (
                                f"codegraph schema 版本 v{db_version} > query-knowledge 支持的最高版本 v{self.SUPPORTED_SCHEMA_VERSION}。\n"
                                f"  → 这是 query-knowledge 版本太老，**不要重建 codegraph**！\n"
                                f"  → 请更新 query-knowledge：`git -C ~/.claude/skills/query-knowledge pull` "
                                f"或重新安装 query-knowledge skill"
                            )
                            # 同时输出到 stderr 让用户/Agent 立即看到
                            import sys as _sys
                            print(
                                f"⚠️  [query-knowledge] codegraph schema v{db_version} 比支持的 v{self.SUPPORTED_SCHEMA_VERSION} 新，"
                                f"请更新 query-knowledge（不要重建 codegraph）",
                                file=_sys.stderr,
                            )
                            return False
                        if db_version < self.MIN_SCHEMA_VERSION:
                            self.unavailable_reason = (
                                f"codegraph schema 版本 v{db_version} < 最低支持版本 v{self.MIN_SCHEMA_VERSION}。\n"
                                f"  → codegraph 索引太老，请重建：`rm -rf .codegraph/ && 重启 daemon`"
                            )
                            return False
            return True
        except Exception as e:
            self.unavailable_reason = f"DB 读取异常: {e}"
            return False

    def _check_schema_version(self) -> bool:
        """兼容性保留：实际检测已合并到 is_available()"""
        return self.is_available()

    def stats(self) -> dict:
        """codegraph DB 统计"""
        if not self.is_available():
            return {
                "error": "codegraph DB 不可用",
                "reason": self.unavailable_reason,
                "db": str(self.db_path),
            }
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM nodes")
            nodes = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM edges")
            edges = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM files")
            files = cur.fetchone()[0]
        return {"nodes": nodes, "edges": edges, "files": files, "db": str(self.db_path)}

    def search(self, query: str, kind: str | None = None, limit: int = 20) -> list[dict]:
        """FTS5 全文搜索符号

        Args:
            query: 搜索词（支持 FTS5 语法，如 Handler*）
            kind: 限定类型（function/class/method/constant 等）
            limit: 返回上限
        """
        with self._conn() as conn:
            cur = conn.cursor()
            # 转义 FTS5 特殊字符
            safe_q = query.replace('"', '""')
            # FTS5 虚拟表用 rowid JOIN nodes.rowid（兼容 content='nodes' 外部内容表）
            sql = """
                SELECT n.id, n.name, n.kind, n.qualified_name, n.file_path,
                       n.start_line, n.docstring, n.signature
                FROM nodes_fts fts
                JOIN nodes n ON n.rowid = fts.rowid
                WHERE nodes_fts MATCH ?
            """
            params: list = [f'"{safe_q}"']
            if kind:
                sql += " AND n.kind = ?"
                params.append(kind)
            sql += " LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            return [self._row_to_node(r) for r in cur.fetchall()]

    def callers(self, symbol: str, limit: int = 20) -> list[dict]:
        """谁调用了 symbol（精确匹配 qualified_name）"""
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT e.source, e.kind, e.line, n.file_path, n.start_line
                   FROM edges e
                   LEFT JOIN nodes n ON e.source = n.qualified_name
                   WHERE e.target = ?
                   LIMIT ?""",
                (symbol, limit),
            )
            return [
                {
                    "caller": r[0],
                    "edge_kind": r[1],
                    "call_line": r[2],
                    "file": r[3] or "(unresolved)",
                    "source_line": r[4],
                }
                for r in cur.fetchall()
            ]

    def callees(self, symbol: str, limit: int = 20) -> list[dict]:
        """symbol 调用了什么"""
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT e.target, e.kind, e.line, n.file_path, n.start_line
                   FROM edges e
                   LEFT JOIN nodes n ON e.target = n.qualified_name
                   WHERE e.source = ?
                   LIMIT ?""",
                (symbol, limit),
            )
            return [
                {
                    "callee": r[0],
                    "edge_kind": r[1],
                    "call_line": r[2],
                    "file": r[3] or "(unresolved)",
                    "source_line": r[4],
                }
                for r in cur.fetchall()
            ]

    def impact(self, symbol: str, limit: int = 50) -> dict:
        """symbol 的完整影响面：callers + 被引用文件列表"""
        callers = self.callers(symbol, limit=limit)
        files = sorted({c["file"] for c in callers if c["file"] != "(unresolved)"})
        return {
            "symbol": symbol,
            "callers_count": len(callers),
            "affected_files": files,
            "callers": callers,
        }

    def _row_to_node(self, row) -> dict:
        return {
            "id": row[0],
            "name": row[1],
            "kind": row[2],
            "qualified_name": row[3],
            "file": row[4],
            "line": row[5],
            "docstring": (row[6] or "")[:200],
            "signature": row[7] or "",
        }


# ──────────────────────────────────────────────
# Predev 后端 — 错题本匹配（pre-dev-checklist 规则）
# ──────────────────────────────────────────────

class PredevMatcher:
    """封装 pre-dev-checklist 的错题本匹配规则

    不调用 Skill（避免 description 冲突），直接复用相同的检索来源和规则：
      - .claude/artifacts/bugs/*/knowledge/*.md  (Bug 修复归档)
      - ~/.claude/projects/<hash>/memory/feedback-*.md  (全局反馈记忆)

    匹配规则（from pre-dev-checklist SKILL.md Step 2）：
      变更涉及 → 匹配关键词表
    """

    name = "predev"

    # 关键词匹配表（从 pre-dev-checklist/SKILL.md Step 2 复用，通用规则）
    # 项目特定规则应通过 .query-knowledge.yml 配置注入，不写死在这里
    MATCH_RULES = [
        ("vue-router / 路由", ["vue-router", "watch", "component reuse", "route"]),
        ("Pydantic Schema", ["schema", "Create", "Update", "Response", "asymmetry"]),
        ("Pinia Store", ["store", "state", "persist", "refresh", "pinia"]),
        ("SSE / 流式", ["SSE", "stream", "refresh", "overwrite", "lifecycle"]),
        ("SQLAlchemy", ["selectinload", "lazy load", "async", "relationship"]),
        ("前端组件生命周期", ["onMounted", "watch", "composable", "cleanup"]),
        ("后端 API 端点", ["endpoint", "response_model", "schema", "validation"]),
        ("中英文处理", ["tokenization", "chinese", "nlp", "split"]),
        ("CLI / 子进程", ["cli", "subprocess", "spread", "dict", "args"]),
        ("硬编码路径", ["hardcoded", "scan", "path", "config"]),
    ]

    def __init__(self, repo_root: Path, config: "QueryKnowledgeConfig | None" = None):
        """额外规则通过 config.predev_extra_rules() 注入（来自 .query-knowledge.yml）"""
        self.repo_root = repo_root
        self.config = config or QueryKnowledgeConfig(repo_root)
        self.artifacts_dir = self.config.predev_artifacts_dir()
        self.feedback_dir = self._find_feedback_dir()
        # 合并通用规则 + 项目特定规则（来自配置）
        self.match_rules = list(self.MATCH_RULES)
        for rule in self.config.predev_extra_rules():
            self.match_rules.append((rule.get("rule", ""), rule.get("keywords", [])))

    def _find_feedback_dir(self) -> Path | None:
        """找 ~/.claude/projects/<hash>/memory/feedback-*.md"""
        home = Path.home()
        proj_hash = str(self.repo_root).replace("/", "-")
        candidate = home / ".claude" / "projects" / proj_hash / "memory"
        if candidate.exists():
            return candidate
        return None

    def is_available(self) -> bool:
        """predev 后端总是可用（规则匹配不依赖文件存在）"""
        return True

    def search(self, query: str, **kwargs) -> list[dict]:
        """Backend Protocol 兼容：把 query 当作 --tech 来匹配规则"""
        scope = {
            "layers": kwargs.get("layers", []),
            "technologies": [query] + kwargs.get("technologies", []),
            "patterns": kwargs.get("patterns", []),
            "files": kwargs.get("files", []),
        }
        result = self.scan_scope(scope)
        # 转换为统一的 hit 列表
        return [
            {"rule": r["rule"], "matched_terms": r["matched_terms"]}
            for r in result["matched_rules"]
        ]

    def stats(self) -> dict:
        """predev 统计：规则数 + 知识文件数"""
        knowledge_count = 0
        if self.artifacts_dir.exists():
            knowledge_count = sum(1 for _ in self.artifacts_dir.rglob("knowledge/*.md"))
        return {
            "rules_count": len(self.match_rules),
            "knowledge_files_count": knowledge_count,
            "artifacts_dir": str(self.artifacts_dir.relative_to(self.repo_root))
            if self.artifacts_dir.exists() else None,
        }

    def scan_scope(self, scope: dict) -> dict:
        """根据变更范围检索匹配的错题本

        Args:
            scope: {"layers": [...], "technologies": [...], "patterns": [...], "files": [...]}

        Returns:
            {"matched_rules": [...], "knowledge_files": [...], "checklist": [...]}
        """
        matched_rules = []
        scope_terms = set()
        for k in ("layers", "technologies", "patterns"):
            for v in scope.get(k, []):
                scope_terms.add(str(v).lower())
        for f in scope.get("files", []):
            ext = Path(f).suffix.lower()
            if ext:
                scope_terms.add(ext)

        # 规则匹配：任意 scope_term 命中规则关键词
        for rule_name, kws in self.match_rules:
            kws_lower = [k.lower() for k in kws]
            hit_terms = [t for t in scope_terms if any(kw in t or t in kw for kw in kws_lower)]
            if hit_terms:
                matched_rules.append({
                    "rule": rule_name,
                    "matched_terms": hit_terms,
                    "keywords": kws,
                })

        # 扫描知识文件
        knowledge_files = self._scan_knowledge_files(scope_terms)
        # 生成检查清单
        checklist = self._build_checklist(matched_rules, knowledge_files)

        return {
            "scope": scope,
            "matched_rules_count": len(matched_rules),
            "matched_rules": matched_rules,
            "knowledge_files_count": len(knowledge_files),
            "knowledge_files": knowledge_files,
            "checklist": checklist,
        }

    def _scan_knowledge_files(self, scope_terms: set) -> list[dict]:
        """扫描 .claude/artifacts/bugs/*/knowledge/*.md，匹配 scope_terms"""
        results = []
        if not self.artifacts_dir.exists():
            return results
        for md in self.artifacts_dir.rglob("knowledge/*.md"):
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            text_lower = text.lower()
            hits = [t for t in scope_terms if t in text_lower]
            if hits:
                results.append({
                    "path": str(md.relative_to(self.repo_root)),
                    "matched_terms": hits[:10],
                    "line_count": text.count("\n") + 1,
                })
        return sorted(results, key=lambda r: len(r["matched_terms"]), reverse=True)

    def _build_checklist(self, rules: list[dict], kfiles: list[dict]) -> list[str]:
        """生成针对性 checklist"""
        items = []
        if rules:
            items.append(f"### 匹配规则 ({len(rules)} 条)")
            for r in rules[:5]:
                items.append(f"- **{r['rule']}** (命中: {', '.join(r['matched_terms'][:3])})")
        if kfiles:
            items.append(f"\n### 参考错题本 ({len(kfiles)} 份)")
            for kf in kfiles[:5]:
                items.append(f"- `{kf['path']}` (命中 {len(kf['matched_terms'])} 关键词)")
        if not rules and not kfiles:
            items.append("未匹配到任何已知错题模式 — 本次变更可能比较新颖，记得沉淀经验！")
        return items


# ──────────────────────────────────────────────
# CLI 命令
# ──────────────────────────────────────────────

def cmd_overview(idx: KbIndex, args) -> dict:
    """返回 KB 全貌：分类清单 + 每分类 L2 摘要 + 配置来源"""
    root_readme = idx.by_layer["L1"]
    # 标识 KB 路径来源（auto-detect / yml 配置 / 未找到）
    kb_path_str = str(idx.kb_root.relative_to(idx.repo_root)) if idx.kb_root else None
    yml_path = idx.repo_root / ".query-knowledge.yml"
    json_path = idx.repo_root / ".query-knowledge.json"
    if yml_path.exists() or json_path.exists():
        kb_source = "config-file"
    elif kb_path_str:
        kb_source = "auto-detect"
    else:
        kb_source = "not-found"
    overview = {
        "kb_root": kb_path_str,
        "kb_source": kb_source,  # auto-detect / config-file / not-found
        "total_docs": len(idx.docs),
        "total_lines": sum(d.line_count for d in idx.docs),
        "layers": {l: len(idx.docs_in_layer(l)) for l in ["L1", "L2", "L3"]},
        "categories": [],
    }
    categories: dict[str, list[KbDoc]] = {}
    for d in idx.docs:
        categories.setdefault(d.category, []).append(d)

    for cat, docs in sorted(categories.items()):
        cat_info = {
            "category": cat,
            "doc_count": len(docs),
            "l2_summary": None,
            "l3_titles": [],
        }
        for d in docs:
            if d.layer == "L2":
                cat_info["l2_summary"] = {
                    "title": d.title,
                    "summary": d.summary,
                    "path": d.path,
                }
            elif d.layer == "L3":
                cat_info["l3_titles"].append(d.title)
        overview["categories"].append(cat_info)
    return overview


def KbIndex_docs_in_layer(self, layer: str) -> list[KbDoc]:
    return self.by_layer.get(layer, [])


# monkey-patch 简化调用
KbIndex.docs_in_layer = KbIndex_docs_in_layer


def cmd_list(idx: KbIndex, args) -> dict:
    return {
        "categories": sorted({d.category for d in idx.docs}),
        "total_docs": len(idx.docs),
    }


def cmd_category(idx: KbIndex, args) -> dict:
    docs = [d for d in idx.docs if d.category == args.name]
    return {
        "category": args.name,
        "docs": [asdict(d) for d in docs],
    }


def cmd_search(idx: KbIndex, args) -> dict:
    hits = idx.search(
        args.query,
        layer_filter=args.layer,
        context=args.context,
        top=args.top,
    )
    return {
        "query": args.query,
        "context": args.context,
        "hit_count": len(hits),
        "hits": [
            {
                "score": h.score,
                "path": h.doc.path,
                "layer": h.doc.layer,
                "title": h.doc.title,
                "summary": h.doc.summary,
                "matched_keywords": h.matched_keywords,
                "matched_sections": h.matched_sections,
                "snippet": h.snippet,
            }
            for h in hits
        ],
    }


def _kb_cache_path(repo_root: Path, task_id: str | None) -> Path:
    """定位 KB 检索缓存文件（D2 修复）。

    有 TASK_ID -> control-plane-state/session-drafts/<TASK_ID>/kb-cache.json
    无 TASK_ID -> .runtime/kb-cache.json

    安全：task_id 必须通过白名单校验（[A-Za-z0-9_-]+），拒绝路径穿越。
    """
    if task_id:
        # 白名单 + 长度校验（防路径穿越 + 文件系统单段名长度限制）
        import re
        MAX_TASK_ID_LEN = 100
        if len(task_id) > MAX_TASK_ID_LEN:
            raise ValueError(
                f"task_id 长度超过 {MAX_TASK_ID_LEN} 字符（防超长文件名），当前长度: {len(task_id)}"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
            raise ValueError(
                f"task_id 必须由字母/数字开头，后跟 ≤127 字符的 [A-Za-z0-9._-]（防路径穿越，对齐 control-plane _SLUG_RE），当前: {task_id!r}"
            )
        d = repo_root / "control-plane-state" / "session-drafts" / task_id
    else:
        d = repo_root / ".runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d / "kb-cache.json"


def _kb_cache_load(cache_path: Path) -> dict:
    """加载缓存，损坏时返回空 dict（overview 显式为 None，调用方需 or {} 兜底）。"""
    try:
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {"overview": None, "searches": [], "pd_scans": []}


# searches 列表上限（FIFO 淘汰：保留最近 N 条，淘汰最早追加的，防缓存无限增长）
_KB_CACHE_MAX_SEARCHES = 50


def _kb_cache_save(cache_path: Path, cache: dict) -> None:
    """原子写入缓存（PID 后缀防并发竞态）。"""
    # 淘汰旧 searches，只保留最近 N 条
    if len(cache.get("searches", [])) > _KB_CACHE_MAX_SEARCHES:
        cache["searches"] = cache["searches"][-_KB_CACHE_MAX_SEARCHES:]
    tmp = cache_path.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_path)


def _kb_content_hash(repo_root: Path, config: "QueryKnowledgeConfig") -> str:
    """计算 KB 内容 hash（用于判断 overview 是否可复用）。

    基于 KB 目录下所有 .md 文件的路径+修改时间，避免每次重算全文 hash。
    """
    import hashlib
    kb_path = config.kb_path()
    if not kb_path or not Path(kb_path).exists():
        return "no-kb"
    h = hashlib.sha256()
    for md in sorted(Path(kb_path).rglob("*.md")):
        rel = md.relative_to(repo_root).as_posix()
        mtime = md.stat().st_mtime
        h.update(f"{rel}:{mtime}".encode())
    return h.hexdigest()[:16]


def cmd_kb_aware(idx: KbIndex, args) -> dict:
    """kb-aware: 一次调用完成 overview + search，内置缓存（D2/T3 修复）。

    替代 deep-planning/team 各自内联的 overview+search 重复调用。
    缓存命中时复用结果，跳过实际检索，节省 ~1.5k tokens/次。

    返回:
      {
        "overview": {..., "cached": bool},
        "search": {..., "cached": bool},
        "cache_path": "...",
        "kb_content_hash": "..."
      }
    """
    repo_root = idx.repo_root
    task_id = getattr(args, "task_id", None)
    cache_path = _kb_cache_path(repo_root, task_id)
    cache = _kb_cache_load(cache_path)
    content_hash = _kb_content_hash(repo_root, idx.config)

    # overview: 按 KB 内容 hash 判断是否可复用
    # 显式 isinstance 检查（不依赖 or {} 隐式兜底，防 None/非 dict 类型）
    cached_ov = cache.get("overview")
    ov_cached = (isinstance(cached_ov, dict)
                 and cached_ov.get("kb_content_hash") == content_hash
                 and isinstance(cached_ov.get("data"), dict))
    if ov_cached:
        overview = dict(cached_ov["data"])  # copy 防污染缓存内对象
        overview["cached"] = True
    else:
        overview = cmd_overview(idx, args)
        overview["cached"] = False
        cache["overview"] = {"kb_content_hash": content_hash, "data": overview}

    # search: 按 keywords+context 判断是否可复用
    keywords = getattr(args, "task_keywords", "") or ""
    context = getattr(args, "context", "feature") or "feature"
    top = getattr(args, "top", 5) or 5
    cached_search = None
    for s in cache.get("searches", []):
        if isinstance(s, dict) and s.get("keywords") == keywords and s.get("context") == context and s.get("top") == top:
            cached_search = s
            break
    if cached_search and isinstance(cached_search.get("data"), dict):
        search_result = dict(cached_search["data"])  # copy 防污染
        search_result["cached"] = True
    else:
        if keywords:
            # 用 copy 避免修改原始 args（防副作用污染 main 调用链）
            import copy
            search_args = copy.copy(args)
            search_args.query = keywords
            search_args.top = top
            search_args.layer = getattr(args, "kb_layer", None)
            search_result = cmd_search(idx, search_args)
        else:
            search_result = {"query": "", "hit_count": 0, "hits": [], "note": "no task_keywords provided"}
        search_result["cached"] = False
        cache.setdefault("searches", []).append({
            "keywords": keywords, "context": context, "top": top, "data": search_result,
        })

    _kb_cache_save(cache_path, cache)

    return {
        "overview": overview,
        "search": search_result,
        "cache_path": str(cache_path.relative_to(repo_root)) if cache_path.is_relative_to(repo_root) else str(cache_path),
        "kb_content_hash": content_hash,
        "task_id": task_id,
    }


def cmd_stats(idx: KbIndex, args) -> dict:
    by_cat: dict[str, int] = {}
    for d in idx.docs:
        by_cat[d.category] = by_cat.get(d.category, 0) + 1
    return {
        "total_docs": len(idx.docs),
        "total_lines": sum(d.line_count for d in idx.docs),
        "by_layer": {l: len(idx.docs_in_layer(l)) for l in ["L1", "L2", "L3"]},
        "by_category": by_cat,
    }


def cmd_read(idx: KbIndex, args) -> dict:
    target = idx.repo_root / args.path
    if not target.exists():
        return {"error": f"文件不存在: {args.path}"}
    text = target.read_text(encoding="utf-8", errors="ignore")
    return {
        "path": args.path,
        "line_count": text.count("\n") + 1,
        "content": text,
    }


def cmd_hierarchy(idx: KbIndex, args) -> dict:
    tree: dict[str, dict[str, list[str]]] = {}
    for d in idx.docs:
        tree.setdefault(d.category, {"L2": [], "L3": []})
        if d.layer == "L2":
            tree[d.category]["L2"].append(d.title)
        elif d.layer == "L3":
            tree[d.category]["L3"].append(d.title)
    return {"hierarchy": tree}


COMMANDS = {
    "overview":  cmd_overview,
    "list":      cmd_list,
    "category":  cmd_category,
    "search":    cmd_search,
    "stats":     cmd_stats,
    "read":      cmd_read,
    "hierarchy": cmd_hierarchy,
    "kb-aware":  cmd_kb_aware,
}


# ──────────────────────────────────────────────
# 参数归一化（让用户怎么写都行）
# ──────────────────────────────────────────────

# 全局参数白名单：这些参数无论出现在哪都视为全局参数
# 格式: {参数名: 是否带值}
GLOBAL_FLAGS = {
    "--repo":       True,   # 带值
    "--backend":    True,
    "--json":       False,  # 不带值
    "--layer":      True,
    "--tech":       True,
    "--pattern":    True,
    "--file":       True,
    "--top":        True,
    "--context":    True,
    "--limit":      True,
    "--kind":       True,
}

# 子命令白名单
SUBCOMMANDS = {
    "overview", "list", "stats", "hierarchy", "category", "search", "read",
    "cg-stats", "cg-search", "cg-callers", "cg-callees", "cg-impact",
    "pd-scan", "kb-aware",
}


def _normalize_argv(argv: list[str]) -> list[str]:
    """重排 argv：全局参数提前，子命令及其专属参数在后

    让用户可以这样写：
      query_knowledge.py search "x" --top 3        ← 自动重排为
      query_knowledge.py --top 3 search "x"        ← argparse 能识别

    算法：扫描 argv，全局参数（无论在子命令前后）都收集到 global_part，
    子命令及其专属参数收集到 sub_part。子命令位置参数（非 -- 开头）
    保持原顺序在 sub_part 里。
    """
    global_part: list[str] = []
    sub_part: list[str] = []
    sub_found = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if not sub_found and arg in SUBCOMMANDS:
            sub_found = True
            sub_part.append(arg)
        elif arg in GLOBAL_FLAGS:
            # 全局参数（无论子命令前后）都提取到前面
            global_part.append(arg)
            if GLOBAL_FLAGS[arg]:
                if i + 1 < len(argv):
                    global_part.append(argv[i + 1])
                    i += 1
        else:
            # 子命令位置参数或未知参数
            sub_part.append(arg)
        i += 1
    return global_part + sub_part


def resolve_repo_root(arg_root: str | None = None) -> Path:
    """把 --repo 参数解析为真正的仓库根（P0-1）。

    Agent / subagent 常在子目录执行 `--repo .`，此时 . 解析到子目录，
    auto-detect 会误索引子目录下的子集 KB（如 control_plane/docs/kb/）。
    向上探测最近含 .git 的祖先可恢复正确仓库根：
      - 普通仓库：.git 是目录
      - git worktree：.git 是文本文件（指向主仓库 gitdir），exists() 同样覆盖
    探测不到 git 根才兜底原值（如 /tmp 临时测试仓库）。
    """
    start = Path(arg_root or ".").resolve()
    for p in (start, *start.parents):
        if (p / ".git").exists():
            return p
    return start


def _project_note_unavailable(config: QueryKnowledgeConfig) -> str | None:
    """project-note 后端不可用原因；可用时返回 None（P0-2 三态）。

    不可用（返回原因字符串，调用方输出 unavailable，绝不静默返回 0 命中）：
      - 后端未启用
      - 未配置 kb_path（auto-detect 也找不到）
      - kb_path 指向不存在的目录
      - kb_path 指向的目录为空（无 .md 文件）

    可用时返回 None —— 此时 search 返回 hit_count: 0 是真实无匹配，
    与「检索不可用」在结构上可区分。
    """
    if not config.is_backend_enabled("project-note"):
        return "project-note 后端未启用"
    kb_path = config.kb_path()
    if not kb_path:
        return "未找到 KB（在 .query-knowledge.yml 配置 kb_path，或建 docs/project-note/）"
    if not kb_path.exists():
        return f"KB 目录不存在: {kb_path}"
    if not any(kb_path.rglob("*.md")):
        return f"KB 目录为空（无 .md 文件）: {kb_path}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="query_knowledge",
        description="统一检索项目知识背景（KB + codegraph + predev）",
    )
    parser.add_argument(
        "--repo", default=".",
        help="项目根目录（默认当前目录；任意子目录下传 . 会向上探测 git 根）",
    )
    parser.add_argument(
        "--backend",
        choices=["project-note", "codegraph", "predev", "all"],
        default="project-note",
        help="检索后端：project-note(KB) / codegraph(符号级) / predev(错题本) / all(聚合)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="输出 JSON 格式（供其他 Skill 解析）",
    )
    # all-mode / predev-mode 通用参数（提前到全局，方便聚合模式复用）
    parser.add_argument(
        "--layer", action="append", default=[],
        help="KB 层过滤（L1/L2/L3）或 predev 涉及层",
    )
    parser.add_argument(
        "--tech", action="append", default=[],
        help="涉及技术（可多次）",
    )
    parser.add_argument(
        "--pattern", action="append", default=[],
        help="涉及模式（可多次）",
    )
    parser.add_argument(
        "--file", action="append", default=[],
        help="涉及文件（可多次）",
    )
    parser.add_argument(
        "--top", type=int,
        help="search 模式返回上限",
    )
    parser.add_argument(
        "--context",
        choices=["bugfix", "feature"],
        default="feature",
        help="查询场景：bugfix 时错题本加权",
    )
    parser.add_argument(
        "--limit", type=int, default=20,
        help="codegraph search/callers/callees/impact 返回上限",
    )
    parser.add_argument(
        "--kind", default=None,
        help="codegraph search 类型过滤（function/class/method）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ── project-note 子命令 ──
    sub.add_parser("overview", help="KB 全貌")
    sub.add_parser("list", help="列出所有分类")
    sub.add_parser("stats", help="KB 统计")
    sub.add_parser("hierarchy", help="打印三层结构")

    p_cat = sub.add_parser("category", help="查看某分类下所有文档")
    p_cat.add_argument("name", help="分类名，如 '03-核心数据业务流'")

    p_search = sub.add_parser("search", help="按关键词搜索 KB")
    p_search.add_argument("query", help="查询词，可用空格分多关键词")
    # KB 层过滤（区别于全局 --layer 用于 predev 的变更层）
    p_search.add_argument(
        "--kb-layer",
        choices=["L1", "L2", "L3"],
        dest="kb_layer",
        help="KB 层过滤",
    )

    p_read = sub.add_parser("read", help="读取指定文档全文")
    p_read.add_argument("path", help="相对路径，如 docs/project-note/01-架构总览/系统分层架构.md")

    # ── codegraph 子命令 ──
    # --limit 提升到全局；--kind 仅 cg-search 使用，留子命令定义
    p_cg_stats = sub.add_parser("cg-stats", help="codegraph DB 统计")

    p_cg_search = sub.add_parser("cg-search", help="codegraph 符号搜索")
    p_cg_search.add_argument("query", help="搜索词（FTS5 语法）")
    # --kind 已提升到全局

    p_cg_callers = sub.add_parser("cg-callers", help="谁调用了某符号")
    p_cg_callers.add_argument("symbol", help="符号的 qualified_name")

    p_cg_callees = sub.add_parser("cg-callees", help="某符号调用了什么")
    p_cg_callees.add_argument("symbol", help="符号的 qualified_name")

    p_cg_impact = sub.add_parser("cg-impact", help="符号的完整影响面")
    p_cg_impact.add_argument("symbol", help="符号的 qualified_name")

    # ── predev 子命令 ──
    # pd-scan 的所有参数已提升到全局（--layer/--tech/--pattern/--file）
    sub.add_parser("pd-scan", help="按变更范围扫描错题本")

    # ── kb-aware 子命令（D2/T3 修复：统一 KB 感知入口 + 缓存）──
    p_kb_aware = sub.add_parser("kb-aware", help="一次完成 overview+search，内置缓存（替代 deep-planning/team 重复调用）")
    p_kb_aware.add_argument("--task-keywords", default="", help="任务关键词（用于 search，空则只返回 overview）")
    p_kb_aware.add_argument("--task-id", default=None, help="控制面 TASK_ID（用于缓存路径隔离）")

    args = parser.parse_args(_normalize_argv(sys.argv[1:]))

    # --repo 向上探测 git 根，避免子目录误索引子集 KB（P0-1）
    repo_root = resolve_repo_root(args.repo)
    config = QueryKnowledgeConfig(repo_root)

    # ── 路由到对应后端 ──
    if args.backend == "project-note":
        # 三态：未启用 / KB 缺失或为空 → unavailable（绝不静默返回 0 命中）
        unavailable = _project_note_unavailable(config)
        if unavailable:
            if args.json:
                print(json.dumps(
                    {"unavailable": True, "unavailable_reason": unavailable},
                    ensure_ascii=False, indent=2,
                ))
            else:
                print(f"❌ {unavailable}", file=sys.stderr)
            return 2
        idx = KbIndex(repo_root, config)
        handler = COMMANDS[args.cmd]
        if handler is None:
            print(f"❌ 命令 {args.cmd} 不适用于 --backend=project-note", file=sys.stderr)
            return 2
        # KB search 用 --kb-layer 而非全局 --layer
        if args.cmd == "search":
            args.layer = getattr(args, "kb_layer", None)
        try:
            result = handler(idx, args)
        except ValueError as e:
            # cmd_kb_aware 在 task_id 路径穿越时会抛 ValueError
            print(f"❌ {e}", file=sys.stderr)
            return 2
        if not args.json:
            return _print_human(args, result)

    elif args.backend == "codegraph":
        cg = CodegraphIndex(repo_root, config)
        result = _dispatch_codegraph(cg, args)
        if not args.json:
            return _print_human(args, result)

    elif args.backend == "predev":
        pm = PredevMatcher(repo_root, config)
        if args.cmd != "pd-scan":
            print(f"❌ --backend=predev 仅支持 pd-scan 命令", file=sys.stderr)
            return 2
        scope = {
            "layers": args.layer,
            "technologies": args.tech,
            "patterns": args.pattern,
            "files": args.file,
        }
        result = pm.scan_scope(scope)
        if not args.json:
            return _print_human(args, result)

    elif args.backend == "all":
        # 聚合：KB search + codegraph search + predev scan
        result = _dispatch_all(repo_root, config, args)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _dispatch_codegraph(cg: CodegraphIndex, args) -> dict:
    """codegraph 后端的命令分发"""
    if args.cmd == "cg-stats":
        return cg.stats()
    if args.cmd == "cg-search":
        return {"query": args.query, "results": cg.search(args.query, args.kind, args.limit)}
    if args.cmd == "cg-callers":
        return {"symbol": args.symbol, "callers": cg.callers(args.symbol, args.limit)}
    if args.cmd == "cg-callees":
        return {"symbol": args.symbol, "callees": cg.callees(args.symbol, args.limit)}
    if args.cmd == "cg-impact":
        return cg.impact(args.symbol, args.limit)
    raise ValueError(f"codegraph 后端不支持命令: {args.cmd}")


def _dispatch_all(repo_root: Path, config: QueryKnowledgeConfig, args) -> dict:
    """--backend=all 聚合模式

    返回语义（按笨蛋要求）：
      - 后端可用且有结果 → {"hit_count": N, "hits": [...]}
      - 后端可用但无结果 → {"hit_count": 0}  ← 空就行
      - 后端不可用       → {"unavailable": "<原因>"}
      - 后端异常         → {"error": "<异常信息>"}
    """
    aggregated = {
        "query": getattr(args, "query", None),
        "backends": {},
    }

    query = getattr(args, "query", "") or ""
    scope = {
        "layers": getattr(args, "layer", []) or [],
        "technologies": getattr(args, "tech", []) or [],
        "patterns": getattr(args, "pattern", []) or [],
        "files": getattr(args, "file", []) or [],
    }

    # 1) project-note 后端
    try:
        unavailable = _project_note_unavailable(config)
        if unavailable:
            aggregated["backends"]["project-note"] = {"unavailable": unavailable}
        else:
            kb = KbIndex(repo_root, config)
            if not kb.is_available():
                aggregated["backends"]["project-note"] = {"unavailable": "KB 构建后不可用（目录存在但无有效文档）"}
            else:
                kb_hits = kb.search(
                    query,
                    layer_filter=getattr(args, "kb_layer", None),
                    context=getattr(args, "context", "feature"),
                )
                aggregated["backends"]["project-note"] = {
                    "hit_count": len(kb_hits),
                    "hits": [
                        {
                            "score": h.score,
                            "path": h.doc.path,
                            "layer": h.doc.layer,
                            "title": h.doc.title,
                            "matched_keywords": h.matched_keywords,
                        }
                        for h in kb_hits[:10]
                    ],
                }
    except Exception as e:
        aggregated["backends"]["project-note"] = {"error": str(e)}

    # 2) codegraph 后端
    try:
        if not config.is_backend_enabled("codegraph"):
            aggregated["backends"]["codegraph"] = {"unavailable": "未启用"}
        else:
            cg = CodegraphIndex(repo_root, config)
            if not cg.is_available():
                aggregated["backends"]["codegraph"] = {
                    "unavailable": cg.unavailable_reason or "DB 不可用"
                }
            elif not query:
                aggregated["backends"]["codegraph"] = {"hit_count": 0}
            else:
                cg_results = cg.search(query, limit=10)
                aggregated["backends"]["codegraph"] = {
                    "hit_count": len(cg_results),
                    "results": cg_results,
                }
    except Exception as e:
        aggregated["backends"]["codegraph"] = {"error": str(e)}

    # 3) predev 后端
    try:
        if not config.is_backend_enabled("predev"):
            aggregated["backends"]["predev"] = {"unavailable": "未启用"}
        else:
            pm = PredevMatcher(repo_root, config)
            if not any(scope.values()):
                aggregated["backends"]["predev"] = {"hit_count": 0}
            else:
                result = pm.scan_scope(scope)
                aggregated["backends"]["predev"] = {
                    "hit_count": result["matched_rules_count"],
                    "matched_rules": result["matched_rules"],
                    "knowledge_files_count": result["knowledge_files_count"],
                    "checklist": result["checklist"],
                }
    except Exception as e:
        aggregated["backends"]["predev"] = {"error": str(e)}

    return aggregated


def _print_human(args, result) -> int:
    """人类可读输出"""
    if args.cmd == "search" and "hits" in result:
        print(f"🔍 命中 {result['hit_count']} 条（context={result.get('context','feature')}）\n")
        for i, h in enumerate(result["hits"], 1):
            print(f"  [{i}] {h['layer']} {h['title']}  score={h['score']}")
            print(f"      📄 {h['path']}")
            if h["matched_keywords"]:
                print(f"      🔑 命中关键词: {', '.join(h['matched_keywords'])}")
            if h["matched_sections"]:
                print(f"      📑 命中章节: {', '.join(h['matched_sections'][:3])}")
            snip = h.get("snippet", {})
            if snip.get("section_title"):
                print(f"      📂 所在章节: {snip['section_title']}")
                if snip.get("section_preview"):
                    preview = snip["section_preview"].replace("\n", " ")[:150]
                    print(f"      💬 {preview}...")
            elif snip.get("full_match"):
                print(f"      💬 ...{snip['full_match']}...")
            print()
        return 0
    if args.cmd in ("overview", "list", "category", "stats", "hierarchy", "read"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.cmd in ("cg-stats", "cg-search", "cg-callers", "cg-callees", "cg-impact"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "pd-scan":
        print(f"📋 错题本扫描结果")
        print(f"   匹配规则: {result['matched_rules_count']} 条")
        for r in result["matched_rules"][:5]:
            print(f"   - {r['rule']} (命中: {', '.join(r['matched_terms'][:3])})")
        print(f"   知识文件: {result['knowledge_files_count']} 份")
        for kf in result["knowledge_files"][:5]:
            print(f"   - {kf['path']} ({len(kf['matched_terms'])} 命中)")
        print(f"\n   检查清单:")
        for item in result["checklist"]:
            print(f"   {item}")
        return 0
    # 兜底
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0



# ──────────────────────────────────────────────
# 后端注册表填充（在所有类定义完成后）
# ──────────────────────────────────────────────
BACKEND_REGISTRY = [KbIndex, CodegraphIndex, PredevMatcher]


if __name__ == "__main__":
    sys.exit(main())