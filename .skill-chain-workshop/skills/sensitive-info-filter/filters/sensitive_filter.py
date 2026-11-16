"""
敏感信息过滤器 - 四道防线实现

Layer 1: 正则规则（结构化凭证）
Layer 2: 熵值检测（未知模式高熵字符串）
Layer 3: Luhn 二次验证（银行卡防误判）
Layer 4: 敏感词库（业务相关）

Author: knowledge-base-builder v1.1
License: MIT
"""
import re
import math
import hashlib
import fnmatch
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class Hit:
    """单次命中记录"""
    type: str            # regex / entropy / luhn / wordlist
    category: str        # 命中类别（手机号/OpenAI Key/...）
    severity: str        # high / medium / low
    position: Tuple[int, int] = (0, 0)  # (start, end) in original text
    preview: str = ""    # 脱敏预览（如 "****901"）
    entropy: float = 0.0 # 熵值（仅 entropy 类型）
    context: str = ""    # 文件路径
    hash: str = ""       # 8 位原文 hash（追踪用，非还原用）


@dataclass
class FilterResult:
    """过滤结果"""
    sanitized: str
    hits: List[Hit] = field(default_factory=list)

    @property
    def has_high(self) -> bool:
        return any(h.severity == "high" for h in self.hits)

    @property
    def has_medium(self) -> bool:
        return any(h.severity == "medium" for h in self.hits)

    @property
    def high_hits(self) -> List[Hit]:
        return [h for h in self.hits if h.severity == "high"]

    def to_dict(self) -> Dict:
        return {
            "sanitized_length": len(self.sanitized),
            "hit_count": len(self.hits),
            "high_count": len(self.high_hits),
            "medium_count": sum(1 for h in self.hits if h.severity == "medium"),
            "low_count": sum(1 for h in self.hits if h.severity == "low"),
            "hits": [asdict(h) for h in self.hits],
        }


# ═══════════════════════════════════════════════════════════════
# 主类
# ═══════════════════════════════════════════════════════════════

class SensitiveFilter:
    """敏感信息过滤器 - 四道防线"""

    # 文件白/黑名单
    SCAN_EXTS: Set[str] = {
        ".md", ".ts", ".tsx", ".js", ".jsx", ".py", ".go",
        ".java", ".kt", ".swift", ".rs", ".txt", ".yaml", ".yml", ".json",
    }
    # Glob 模式支持（fnmatch）— * 匹配任意字符
    SKIP_PATTERNS: Set[str] = {
        ".env", ".env.*",         # .env, .env.production, .env.local
        "*.pem", "*.key", "*.p12", "*.pfx",  # private.pem, server.key 等
        "id_rsa", "id_rsa.pub",
        "credentials.json", "service-account.json",
    }
    SKIP_DIRS: Set[str] = {
        ".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv",
    }

    # 防线 1: 正则规则库（结构化凭证，~90% 拦截率）
    REGEX_RULES: Dict[str, Dict] = {
        # ── PII（个人身份信息）──
        "手机号(中国)": {
            "pattern": r"\b1[3-9]\d{9}\b",
            "severity": "medium",
            "placeholder": "<REDACTED:手机号>",
        },
        "身份证(中国)": {
            "pattern": r"\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
            "severity": "medium",
            "placeholder": "<REDACTED:身份证>",
        },
        "邮箱": {
            # 支持中文 local part 和中文域名（RFC 6531 国际化邮箱）
            "pattern": r"(?<![\w])[A-Za-z0-9._%+\-\u4e00-\u9fff]+@[A-Za-z0-9.\-\u4e00-\u9fff]+\.[A-Za-z\u4e00-\u9fff]{2,}(?![\w])",
            "severity": "medium",
            "placeholder": "<REDACTED:邮箱>",
        },
        # ── 金融 ──
        "银行卡(13-19位数字)": {
            "pattern": r"\b\d{13,19}\b",
            "severity": "medium",  # 需 Luhn 二次验证
            "placeholder": "<REDACTED:银行卡>",
            "needs_luhn": True,
        },
        # ── 网络 ──
        "内网IP": {
            "pattern": r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|127\.0\.0\.1)\b",
            "severity": "medium",
            "placeholder": "<REDACTED:内网IP>",
        },
        # ── 凭证（高危）──
        "AWS Access Key": {
            "pattern": r"AKIA[0-9A-Z]{16}",
            "severity": "high",
            "placeholder": "<REDACTED:AWS_KEY>",
        },
        "AWS Secret Key": {
            "pattern": r"(?i)aws[_-]?secret[_-]?access[_-]?key[_-:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?",
            "severity": "high",
            "placeholder": "<REDACTED:AWS_SECRET>",
        },
        "GitHub Token (ghp_)": {
            "pattern": r"\bghp_[A-Za-z0-9]{36}\b",
            "severity": "high",
            "placeholder": "<REDACTED:GITHUB_TOKEN>",
        },
        "GitHub Token (ghs_)": {
            "pattern": r"\bghs_[A-Za-z0-9]{36}\b",
            "severity": "high",
            "placeholder": "<REDACTED:GITHUB_SERVER_TOKEN>",
        },
        "OpenAI API Key": {
            "pattern": r"\bsk-[A-Za-z0-9]{20,}\b",
            "severity": "high",
            "placeholder": "<REDACTED:OPENAI_KEY>",
        },
        "Anthropic API Key": {
            "pattern": r"\bsk-ant-[A-Za-z0-9\-]{20,}\b",
            "severity": "high",
            "placeholder": "<REDACTED:ANTHROPIC_KEY>",
        },
        "Google API Key": {
            "pattern": r"\bAIza[0-9A-Za-z\-_]{35}\b",
            "severity": "high",
            "placeholder": "<REDACTED:GOOGLE_KEY>",
        },
        "Slack Token": {
            "pattern": r"\bxox[abprs]-[0-9A-Za-z\-]{10,}\b",
            "severity": "high",
            "placeholder": "<REDACTED:SLACK_TOKEN>",
        },
        "Stripe Key": {
            "pattern": r"\b(?:sk|pk|rk)_(?:test|live)_[A-Za-z0-9]{20,}\b",
            "severity": "high",
            "placeholder": "<REDACTED:STRIPE_KEY>",
        },
        "Private Key Block": {
            "pattern": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
            "severity": "high",
            "placeholder": "<REDACTED:PRIVATE_KEY_BLOCK>",
        },
        "Generic API Key Assignment": {
            "pattern": r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|pwd)\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{16,})['\"]?",
            "severity": "high",
            "placeholder": "<REDACTED:API_KEY>",
        },
        "JWT Token": {
            "pattern": r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
            "severity": "high",
            "placeholder": "<REDACTED:JWT>",
        },
    }

    # 防线 2: 熵值阈值（高熵=看起来像密钥）
    ENTROPY_THRESHOLD: float = 4.5  # 经验值
    ENTROPY_MIN_LENGTH: int = 32

    def __init__(self, config_path: Optional[str] = None, wordlist_path: Optional[str] = None):
        """
        初始化过滤器

        Args:
            config_path: regex_rules.yaml 路径（可选，使用内置规则可省略）
            wordlist_path: wordlist.txt 路径（可选）
        """
        self.regex_rules = self.REGEX_RULES.copy()
        self.entropy_threshold = self.ENTROPY_THRESHOLD
        self.entropy_min_length = self.ENTROPY_MIN_LENGTH
        self.sensitive_words: List[str] = []
        self.audit_log: List[Hit] = []

        if wordlist_path and Path(wordlist_path).exists():
            self.sensitive_words = self._load_wordlist(wordlist_path)
        elif config_path and Path(config_path).exists():
            self.sensitive_words = self._load_wordlist(config_path)

    # ──────────────────────────────────────────────────────────
    # 工具方法
    # ──────────────────────────────────────────────────────────

    def _load_wordlist(self, path: str) -> List[str]:
        """加载敏感词库，每行一个（# 开头为注释）"""
        words = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    words.append(line)
        return words

    def _entropy(self, s: str) -> float:
        """计算 Shannon 信息熵"""
        if not s:
            return 0.0
        counter = Counter(s)
        length = len(s)
        return -sum((c / length) * math.log2(c / length) for c in counter.values())

    def _luhn_check(self, card: str) -> bool:
        """Luhn 算法验证银行卡号"""
        digits = [int(d) for d in card if d.isdigit()]
        if len(digits) < 13:
            return False
        # Luhn: 从右往左, 偶数位 × 2, 若是两位数则各位相加
        checksum = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2 == 1:  # 偶数位（从右数 0-indexed）
                d *= 2
                if d > 9:
                    d -= 9
            checksum += d
        return checksum % 10 == 0

    def _short_hash(self, text: str) -> str:
        """8 位短 hash（追踪用）"""
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:8]

    def _make_preview(self, text: str) -> str:
        """生成脱敏预览（不暴露原文）"""
        if len(text) <= 8:
            return "****"
        return text[:4] + "****" + text[-4:]

    # ──────────────────────────────────────────────────────────
    # 防线 1: 正则匹配
    # ──────────────────────────────────────────────────────────

    def _layer_1_regex(self, text: str) -> Tuple[str, List[Hit]]:
        """防线 1: 正则规则匹配"""
        sanitized = text
        hits: List[Hit] = []

        for label, rule in self.regex_rules.items():
            pattern = rule["pattern"]
            severity = rule["severity"]
            placeholder = rule["placeholder"]
            needs_luhn = rule.get("needs_luhn", False)

            try:
                matches = list(re.finditer(pattern, sanitized))
            except re.error:
                continue

            # 倒序遍历避免位置偏移
            for match in reversed(matches):
                matched_text = match.group()

                # 银行卡需要 Luhn 二次验证
                if needs_luhn and not self._luhn_check(matched_text):
                    continue

                # 跳过明显的占位符（熵值过低）
                if matched_text.lower() in {"your_key_here", "your_token_here",
                                             "fake_xxx_xxx", "xxx", "yyyy",
                                             "your_password", "changeme"}:
                    continue

                hit = Hit(
                    type="regex",
                    category=label,
                    severity=severity,
                    position=match.span(),
                    preview=self._make_preview(matched_text),
                )
                hits.append(hit)
                sanitized = sanitized[: match.start()] + placeholder + sanitized[match.end():]

        return sanitized, hits

    # ──────────────────────────────────────────────────────────
    # 防线 2: 熵值检测
    # ──────────────────────────────────────────────────────────

    def _layer_2_entropy(self, text: str) -> Tuple[str, List[Hit]]:
        """防线 2: 高熵字符串检测"""
        sanitized = text
        hits: List[Hit] = []

        # 匹配长度足够的连续字母数字
        pattern = r"[A-Za-z0-9_\-]{" + str(self.entropy_min_length) + r",}"
        try:
            matches = list(re.finditer(pattern, sanitized))
        except re.error:
            return sanitized, hits

        # 倒序处理
        for match in reversed(matches):
            token = match.group()
            entropy = self._entropy(token)

            if entropy >= self.entropy_threshold:
                # 排除已知的非密钥高熵字符串（base64 编码图片/UUID）
                if re.match(r"^[0-9a-f]{32,}$", token) and len(token) == 32:
                    # 32 位 hex 像 MD5，跳过
                    continue
                if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", token):
                    # UUID，跳过
                    continue

                hit = Hit(
                    type="entropy",
                    category="高熵字符串(疑似密钥)",
                    severity="high",
                    position=match.span(),
                    preview=self._make_preview(token),
                    entropy=round(entropy, 2),
                )
                hits.append(hit)
                sanitized = (
                    sanitized[: match.start()]
                    + "<REDACTED:HIGH_ENTROPY>"
                    + sanitized[match.end():]
                )

        return sanitized, hits

    # ──────────────────────────────────────────────────────────
    # 防线 3: Luhn 二次验证（已在 Layer 1 中通过 needs_luhn 标志触发）
    # 保留此方法作为独立调用入口
    # ──────────────────────────────────────────────────────────

    def _layer_3_luhn(self, text: str) -> Tuple[str, List[Hit]]:
        """防线 3: Luhn 算法二次验证（独立调用）"""
        sanitized = text
        hits: List[Hit] = []

        for match in re.finditer(r"\b\d{13,19}\b", sanitized):
            if self._luhn_check(match.group()):
                hit = Hit(
                    type="luhn",
                    category="银行卡号(Luhn验证通过)",
                    severity="medium",
                    position=match.span(),
                    preview="****" + match.group()[-4:],
                )
                hits.append(hit)
                sanitized = sanitized[: match.start()] + "<REDACTED:银行卡>" + sanitized[match.end():]

        return sanitized, hits

    # ──────────────────────────────────────────────────────────
    # 防线 4: 敏感词库
    # ──────────────────────────────────────────────────────────

    def _layer_4_wordlist(self, text: str) -> Tuple[str, List[Hit]]:
        """防线 4: 业务敏感词匹配"""
        sanitized = text
        hits: List[Hit] = []

        for word in self.sensitive_words:
            if word in sanitized:
                # 字面匹配（不去重——同词多次出现也多次记录）
                positions = []
                start = 0
                while True:
                    pos = sanitized.find(word, start)
                    if pos < 0:
                        break
                    positions.append(pos)
                    start = pos + len(word)

                for pos in reversed(positions):
                    hit = Hit(
                        type="wordlist",
                        category=f"敏感词:{word}",
                        severity="low",
                        position=(pos, pos + len(word)),
                        preview="*" * min(len(word), 8),
                    )
                    hits.append(hit)
                    sanitized = sanitized[:pos] + ("*" * len(word)) + sanitized[pos + len(word):]

        return sanitized, hits

    # ──────────────────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────────────────

    def filter_text(self, text: str, context: str = "") -> FilterResult:
        """
        主入口：四道防线串联执行

        Args:
            text: 待过滤文本
            context: 上下文（文件路径等，用于审计日志）

        Returns:
            FilterResult(sanitized, hits)
        """
        all_hits: List[Hit] = []
        text_hash = self._short_hash(text)
        sanitized = text

        # Layer 1: 正则
        sanitized, hits = self._layer_1_regex(sanitized)
        all_hits.extend(hits)

        # Layer 2: 熵值
        sanitized, hits = self._layer_2_entropy(sanitized)
        all_hits.extend(hits)

        # Layer 3: Luhn 已在 Layer 1 中通过 needs_luhn 触发，此处跳过
        # 保留独立调用入口以支持单独使用

        # Layer 4: 敏感词
        sanitized, hits = self._layer_4_wordlist(sanitized)
        all_hits.extend(hits)

        # 补全审计字段
        for hit in all_hits:
            hit.context = context
            hit.hash = text_hash

        # 写入审计日志
        self.audit_log.extend(all_hits)

        return FilterResult(sanitized=sanitized, hits=all_hits)

    # ──────────────────────────────────────────────────────────
    # 文件级操作
    # ──────────────────────────────────────────────────────────

    def should_skip_file(self, file_path: Path) -> bool:
        """判断文件是否应跳过（黑名单）"""
        # 路径中包含跳过目录（任一部分命中）
        for part in file_path.parts:
            if part in self.SKIP_DIRS:
                return True

        # 文件名匹配 glob 模式
        name = file_path.name
        for pattern in self.SKIP_PATTERNS:
            if fnmatch.fnmatch(name, pattern):
                return True

        return False

    def should_scan_file(self, file_path: Path) -> bool:
        """判断文件是否在扫描白名单内"""
        return file_path.suffix.lower() in self.SCAN_EXTS

    def filter_file(
        self, file_path: Path, in_place: bool = False, output_path: Optional[Path] = None
    ) -> FilterResult:
        """
        过滤单个文件

        Args:
            file_path: 待过滤文件
            in_place: 是否原地修改（默认 False，建议显式输出）
            output_path: 输出路径（None 时默认 file_path.with_suffix(file_path.suffix + '.clean'))

        Returns:
            FilterResult
        """
        file_path = Path(file_path)
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        result = self.filter_text(content, context=str(file_path))

        if result.has_high:
            # 高危命中：跳过整个文件
            return result

        if in_place:
            file_path.write_text(result.sanitized, encoding="utf-8")
        elif output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(result.sanitized, encoding="utf-8")

        return result

    def filter_directory(
        self,
        root: Path,
        output_root: Optional[Path] = None,
        dry_run: bool = False,
    ) -> Dict:
        """
        批量过滤目录

        Args:
            root: 待扫描根目录
            output_root: 输出根目录（None 时 in_place）
            dry_run: 仅检测不写入

        Returns:
            {
                "scanned": int, "redacted": int, "blocked": int,
                "skipped": int, "results": List[Dict]
            }
        """
        root = Path(root)
        stats = {
            "scanned": 0,
            "redacted": 0,
            "blocked": 0,
            "skipped": 0,
            "results": [],
        }

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if self.should_skip_file(file_path):
                stats["skipped"] += 1
                continue
            if not self.should_scan_file(file_path):
                continue

            stats["scanned"] += 1

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            result = self.filter_text(content, context=str(file_path.relative_to(root)))
            rel_path = file_path.relative_to(root)

            if result.has_high:
                stats["blocked"] += 1
                stats["results"].append({
                    "file": str(rel_path),
                    "status": "blocked",
                    "hits": len(result.high_hits),
                    "categories": [h.category for h in result.high_hits],
                })
            elif result.hits:
                stats["redacted"] += 1
                stats["results"].append({
                    "file": str(rel_path),
                    "status": "redacted",
                    "hits": len(result.hits),
                    "categories": list(set(h.category for h in result.hits)),
                })

                if not dry_run:
                    if output_root:
                        out_path = Path(output_root) / rel_path
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_text(result.sanitized, encoding="utf-8")
                    else:
                        file_path.write_text(result.sanitized, encoding="utf-8")
            else:
                stats["results"].append({
                    "file": str(rel_path),
                    "status": "clean",
                    "hits": 0,
                })

                if not dry_run and output_root:
                    out_path = Path(output_root) / rel_path
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(result.sanitized, encoding="utf-8")

        return stats

    def evaluate_audit_decision(
        self,
        file_path: str,
        hits: List[Hit],
    ) -> Dict:
        """
        高危 Gate 决策 — 根据命中情况决定后续动作

        ⚠️ 这是关键业务决策点！不同项目/不同合规要求的处理方式可能不同：
          - 是阻断整个文件还是只阻断命中行？
          - 如何区分生产密钥 vs 测试占位符？
          - 是否允许人工 override？

        Args:
            file_path: 命中的文件路径
            hits: 该文件的所有命中列表

        Returns:
            {
                "action": "skip" | "review" | "redact_in_place" | "abort",
                "reason": str,
                "block_writes": bool,  # 是否阻塞下游写入
                "user_prompt": Optional[str],  # 需要展示给用户的问题
            }
        """
        # TODO: 实现 Gate 决策逻辑
        # 参考以下维度：
        #   1. 命中数量 (len(hits))
        #   2. 严重度分布 (high/medium/low count)
        #   3. 命中类型（私钥块 vs API Key vs 占位符示例）
        #   4. 文件类型（.env 必然高危，README.md 可能只是文档示例）
        #
        # 返回示例：
        #   {
        #     "action": "review",       # 让用户决定
        #     "reason": "1 处 high 命中，建议人工审核",
        #     "block_writes": True,
        #     "user_prompt": "⛔ 文件 {file_path} 包含 {category}\n选项：A. 跳过 / B. 保留(确认是占位符) / C. 中止"
        #   }
        raise NotImplementedError(
            "evaluate_audit_decision() 需要用户实现 — "
            "这是高危命中时的 Gate 决策逻辑，决定不同严重度组合下的处理动作"
        )

    def write_audit_log(
        self,
        stats: Dict,
        output_dir: Path,
    ) -> Path:
        """写入审计日志（合规追溯）"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        audit_file = output_dir / f"filter-{timestamp}.json"

        audit_data = {
            "skill": "sensitive-info-filter",
            "version": "1.1.0",
            "timestamp": timestamp,
            "stats": {
                "scanned": stats.get("scanned", 0),
                "redacted": stats.get("redacted", 0),
                "blocked": stats.get("blocked", 0),
                "skipped": stats.get("skipped", 0),
            },
            "results": stats.get("results", []),
        }

        import json
        audit_file.write_text(
            json.dumps(audit_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return audit_file
