"""
敏感信息过滤器测试用例
覆盖：
  - 10+ 凭证类型（API Key/Token/私钥/PII/银行卡）
  - 熵值检测边界条件
  - Luhn 算法正反向
  - 文件黑白名单分流
  - 高危 Gate 决策
"""
import pytest
import tempfile
from pathlib import Path

from filters.sensitive_filter import SensitiveFilter, Hit, FilterResult


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def f():
    """默认过滤器（不加载词库）"""
    return SensitiveFilter()


@pytest.fixture
def f_with_words(tmp_path):
    """带自定义词库的过滤器"""
    wordlist = tmp_path / "wordlist.txt"
    wordlist.write_text(
        "# 业务敏感词\nProject-Phoenix\n客户A\n", encoding="utf-8"
    )
    return SensitiveFilter(wordlist_path=str(wordlist))


# ═══════════════════════════════════════════════════════════════
# Layer 1: 正则规则 — PII
# ═══════════════════════════════════════════════════════════════

class TestPII:
    def test_chinese_phone_number(self, f):
        text = "联系电话：13812345678 或 13987654321"
        out, hits = f.filter_text(text).sanitized, f.filter_text(text).hits
        assert "13812345678" not in out
        assert "13987654321" not in out
        assert any("手机号" in h.category for h in hits)

    def test_chinese_id_card(self, f):
        text = "身份证号：110101199003078888"
        result = f.filter_text(text)
        assert "110101199003078888" not in result.sanitized
        assert any("身份证" in h.category for h in result.hits)

    def test_email_address(self, f):
        text = "联系邮箱：john.doe+test@example.com"
        result = f.filter_text(text)
        assert "john.doe+test@example.com" not in result.sanitized
        assert any("邮箱" in h.category for h in result.hits)

    def test_internal_ip(self, f):
        text = "API 部署在 192.168.1.100 和 10.0.0.5"
        result = f.filter_text(text)
        assert "192.168.1.100" not in result.sanitized
        assert "10.0.0.5" not in result.sanitized


# ═══════════════════════════════════════════════════════════════
# Layer 1: 正则规则 — 凭证（高危）
# ═══════════════════════════════════════════════════════════════

class TestCredentials:
    def test_openai_api_key_high_severity(self, f):
        text = "请使用 OPENAI_KEY=sk-abc123def456ghi789jkl012mno345pqr678stu 调用 API"
        result = f.filter_text(text)
        assert "sk-abc" not in result.sanitized
        assert result.has_high
        assert any(h.category == "OpenAI API Key" for h in result.hits)

    def test_github_token_high_severity(self, f):
        text = "GITHUB_TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        result = f.filter_text(text)
        assert "ghp_" not in result.sanitized
        assert result.has_high

    def test_aws_access_key_high_severity(self, f):
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        result = f.filter_text(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result.sanitized
        assert result.has_high

    def test_private_key_block_high_severity(self, f):
        text = """
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA...
-----END RSA PRIVATE KEY-----
"""
        result = f.filter_text(text)
        assert "BEGIN RSA PRIVATE KEY" not in result.sanitized
        assert result.has_high

    def test_jwt_token_high_severity(self, f):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = f.filter_text(text)
        assert "eyJ" not in result.sanitized
        assert result.has_high

    def test_anthropic_key_high_severity(self, f):
        text = "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        result = f.filter_text(text)
        assert "sk-ant-api03" not in result.sanitized
        assert result.has_high

    def test_placeholder_skipped(self, f):
        """占位符不应被误拦截"""
        text = "API_KEY = os.getenv('API_KEY', 'your_key_here')"
        result = f.filter_text(text)
        # 'your_key_here' 长度 12 < 16 阈值，不会触发 Generic API Key 规则
        # 熵值也不高
        assert not any("High-Entropy" in h.category for h in result.hits)


# ═══════════════════════════════════════════════════════════════
# Layer 3: Luhn 验证（银行卡防误判）
# ═══════════════════════════════════════════════════════════════

class TestLuhn:
    def test_valid_credit_card_redacted(self, f):
        # 测试用 Visa 卡号（过 Luhn）
        text = "信用卡：4111111111111111"
        result = f.filter_text(text)
        assert "4111111111111111" not in result.sanitized
        assert any("银行卡" in h.category for h in result.hits)

    def test_invalid_number_kept(self, f):
        # 13-19 位但不过 Luhn（如 1234567890123）应保留
        text = "订单号：1234567890123"
        result = f.filter_text(text)
        # 1234567890123 不过 Luhn，保留
        assert "1234567890123" in result.sanitized or "<REDACTED" not in result.sanitized

    def test_luhn_algorithm_correctness(self, f):
        assert f._luhn_check("4111111111111111") is True
        assert f._luhn_check("5500000000000004") is True  # Mastercard 测试号
        assert f._luhn_check("1234567890123456") is False
        assert f._luhn_check("123") is False  # 太短


# ═══════════════════════════════════════════════════════════════
# Layer 2: 熵值检测
# ═══════════════════════════════════════════════════════════════

class TestEntropy:
    def test_high_entropy_token_detected(self, f):
        # 长度 ≥ 32 且熵值 ≥ 4.5
        high_entropy = "aB3$kL9mN2pQ5rS8tU1vW4yZ7cF0gH3jM6nP9qR2sT5vW8xY1zA4bC"
        text = f"token={high_entropy}"
        result = f.filter_text(text)
        assert any(h.type == "entropy" for h in result.hits)

    def test_low_entropy_string_kept(self, f):
        # 重复字符熵值低
        text = "password=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # 全 a
        result = f.filter_text(text)
        assert not any(h.type == "entropy" for h in result.hits)

    def test_short_string_not_checked(self, f):
        # 长度 < 32 不检查
        text = "x=aB3$kL9mN2pQ5rS8"  # 16 字符
        result = f.filter_text(text)
        assert not any(h.type == "entropy" for h in result.hits)

    def test_uuid_not_flagged(self, f):
        # UUID 长度 36 但结构明确，跳过
        text = "id=550e8400-e29b-41d4-a716-446655440000"
        result = f.filter_text(text)
        assert not any(h.type == "entropy" for h in result.hits)

    def test_entropy_calculation(self, f):
        assert f._entropy("") == 0.0
        assert f._entropy("aaaa") == 0.0  # 全同
        assert f._entropy("abcd") == 2.0  # 4 个不同字符均匀分布
        # 随机字符串熵值 > 4
        random_str = "aB3$kL9mN2pQ5rS8tU1vW4yZ7cF0gH3jM6nP9qR"
        assert f._entropy(random_str) > 4.0


# ═══════════════════════════════════════════════════════════════
# Layer 4: 敏感词库
# ═══════════════════════════════════════════════════════════════

class TestWordlist:
    def test_business_word_redacted(self, f_with_words):
        text = "项目代号：Project-Phoenix 进展顺利"
        result = f_with_words.filter_text(text)
        assert "Project-Phoenix" not in result.sanitized
        assert "***" in result.sanitized

    def test_chinese_word_redacted(self, f_with_words):
        text = "客户A 的需求文档在 conf 里"
        result = f_with_words.filter_text(text)
        assert "客户A" not in result.sanitized

    def test_no_words_loaded_means_no_match(self, f):
        text = "项目代号：SomeProject 进展顺利"
        result = f.filter_text(text)
        # 默认 f 不加载词库，应保留原文
        assert "SomeProject" in result.sanitized


# ═══════════════════════════════════════════════════════════════
# 文件级操作：黑白名单
# ═══════════════════════════════════════════════════════════════

class TestFileWhitelist:
    def test_env_file_skipped(self, f):
        assert f.should_skip_file(Path(".env")) is True
        assert f.should_skip_file(Path("config/.env.production")) is True

    def test_pem_file_skipped(self, f):
        assert f.should_skip_file(Path("private.pem")) is True
        assert f.should_skip_file(Path("server.key")) is True

    def test_git_dir_skipped(self, f):
        assert f.should_skip_file(Path(".git/config")) is True
        assert f.should_skip_file(Path("project/.git/HEAD")) is True

    def test_node_modules_skipped(self, f):
        assert f.should_skip_file(Path("node_modules/lodash/index.js")) is True

    def test_md_file_scannable(self, f):
        assert f.should_scan_file(Path("README.md")) is True
        assert f.should_scan_file(Path("docs/architecture.md")) is True

    def test_py_file_scannable(self, f):
        assert f.should_scan_file(Path("src/main.py")) is True

    def test_binary_exe_not_scannable(self, f):
        assert f.should_scan_file(Path("build/output.exe")) is False


# ═══════════════════════════════════════════════════════════════
# 审计日志
# ═══════════════════════════════════════════════════════════════

class TestAuditLog:
    def test_audit_records_hash_not_plaintext(self, f):
        text = "OPENAI_KEY=sk-abc123def456ghi789jkl012mno345pqr678stu"
        result = f.filter_text(text, context="config.py")
        for hit in result.hits:
            assert hit.hash != ""  # 有 hash
            assert len(hit.hash) == 8  # 8 位短 hash
            assert hit.context == "config.py"
            # 关键：preview 不含原文
            assert "sk-abc123def456" not in hit.preview

    def test_audit_log_appended(self, f):
        assert len(f.audit_log) == 0
        f.filter_text("电话：13812345678", context="a.md")
        f.filter_text("邮箱：test@example.com", context="b.md")
        assert len(f.audit_log) == 2

    def test_short_hash_uniqueness(self, f):
        h1 = f._short_hash("hello world")
        h2 = f._short_hash("hello world")
        h3 = f._short_hash("hello WORLD")
        assert h1 == h2
        assert h1 != h3


# ═══════════════════════════════════════════════════════════════
# 严重度分级
# ═══════════════════════════════════════════════════════════════

class TestSeverity:
    def test_high_severity_blocks_file(self, f):
        text = "key=sk-prod1234567890abcdefghijklmnopqrstuvwxyz"
        result = f.filter_text(text)
        assert result.has_high is True
        # 调用方应使用 result.has_high 决定是否跳过

    def test_medium_severity_redacts_in_place(self, f):
        text = "电话：13812345678"
        result = f.filter_text(text)
        assert result.has_medium is True
        assert result.has_high is False
        assert "<REDACTED" in result.sanitized

    def test_low_severity_wordlist(self, f_with_words):
        text = "客户A 项目"
        result = f_with_words.filter_text(text)
        assert any(h.severity == "low" for h in result.hits)


# ═══════════════════════════════════════════════════════════════
# 工具方法
# ═══════════════════════════════════════════════════════════════

class TestUtils:
    def test_make_preview_short(self, f):
        assert f._make_preview("abc") == "****"
        assert f._make_preview("") == "****"

    def test_make_preview_long(self, f):
        # "sk-abc123def456ghi789jkl012mno" 长度 29
        # 期望：前 4 位 + **** + 后 4 位 = "sk-a****2mno"
        preview = f._make_preview("sk-abc123def456ghi789jkl012mno")
        assert preview.startswith("sk-a")
        assert preview.endswith("2mno")  # 后 4 位是 "2mno"
        assert "****" in preview
        assert len(preview) == 12  # 4 + 4 + 4

    def test_make_preview_exact_8(self, f):
        # 长度恰好 8 的情况
        preview = f._make_preview("abcdefgh")
        assert preview == "****"  # 长度 <= 8 返回 ****


# ═══════════════════════════════════════════════════════════════
# 边界条件
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_text(self, f):
        result = f.filter_text("")
        assert result.sanitized == ""
        assert result.hits == []

    def test_text_without_sensitive_data(self, f):
        text = "这是一个普通的技术文档，讨论 React Hooks 的使用。"
        result = f.filter_text(text)
        assert result.sanitized == text
        assert result.hits == []

    def test_multiple_hits_in_one_text(self, f):
        text = """
        OPENAI_KEY=sk-abc123def456ghi789jkl012mno345pqr678stu
        邮箱：test@example.com
        电话：13812345678
        """
        result = f.filter_text(text)
        # 至少 1 个 high（OpenAI Key） + 2 个 medium（邮箱、手机号）
        assert result.has_high
        assert result.has_medium
        assert len(result.hits) >= 3

    def test_unicode_text(self, f):
        text = "邮箱：测试@example.com 📧"
        result = f.filter_text(text)
        assert "测试@example.com" not in result.sanitized
