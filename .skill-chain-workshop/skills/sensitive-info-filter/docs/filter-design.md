# Filter Design — 敏感信息过滤器设计文档 (v1.1)

## 1. 设计目标

**在源代码/Markdown 写入 `docs/project-note/`（AI 共享知识库）之前完成脱敏**，杜绝：
- API Key / Token / 私钥泄露到 LLM 上下文
- PII（手机号/身份证/邮箱）进入知识库
- 银行卡号过 Luhn 验证后被识别
- 内部 IP / 业务敏感词污染知识库

## 2. 为什么是 4 道防线

| 防线 | 拦截目标 | 局限性 | 拦截率 |
|------|---------|--------|-------|
| ① 正则 | 结构化凭证 | 创造性混淆绕过 | ~90% |
| ② 熵值 | 未知模式高熵串 | UUID/MD5 误报 | 补 ~5% |
| ③ Luhn | 银行卡 | 仅银行卡 | 补 ~1% |
| ④ 词库 | 业务敏感词 | 需人工维护 | 补 ~3% |

**为什么不只用正则？** 攻击者可使用：`api_key = base64_decode("c2stYWJjMTIz...")` 绕过正则。熵值检测可识别解码后的密钥。

**为什么不用 LLM 默认审核？** 速度慢（>2s/文件）、Token 贵、不可重现、可能误判。仅作为可选 `--ai-audit` 入口。

## 3. 严重度分级

```
high  (高危)  → 私钥/Token/生产 API Key
                  → 整个文件跳过 + 暂停人工确认
                  → 不自动写入知识库

medium (中危) → 手机号/邮箱/银行卡/内网 IP
                → 替换为 <REDACTED:类型> 占位符
                → 自动写入

low  (低危)  → 业务敏感词（来自 wordlist.txt）
                → 替换为 * 同长度
                → 自动写入
```

## 4. 审计合规

**只存 hash 不存原文**：
- `hash`: 8 位 MD5 短 hash（仅用于追踪同一原文是否重复出现）
- `preview`: 脱敏预览（如 `sk-a****xyz`），不含完整原文
- `position`: 命中位置 (start, end)

**永远不存**：
- 原文（即使在生产环境）
- 完整 hash（避免暴力破解）
- 文件全路径（避免暴露目录结构）

## 5. 性能分析

| 文件 | 大小 | 处理时间 |
|------|-----|---------|
| 单文件 | <10KB | <100ms |
| 批量 | 145 文件 | <5s |
| 大项目 | 1000+ 文件 | <30s |

**瓶颈**: 熵值检测对超长字符串（>1MB）较慢。生产环境可设 `entropy_min_length = 64`。

## 6. 集成方式

### 自动（推荐）

作为 `knowledge-base-builder` Phase 0.5 自动调用。

### 手动

```bash
# 单文件
python -m filters.sensitive_filter README.md

# 批量
python -m filters.sensitive_filter --scan ./docs --output ./docs-clean

# 仅检测
python -m filters.sensitive_filter --scan ./docs --dry-run
```

### CI/CD

```yaml
# .github/workflows/secret-scan.yml
- name: Sensitive Info Filter
  run: |
    python -m filters.sensitive_filter --scan ./docs --dry-run
```

## 7. 未来扩展

- [ ] 自定义正则规则（`--rules` 参数指定 YAML 文件）
- [ ] LLM 上下文判断（默认关闭，`--ai-audit` 启用）
- [ ] Git 历史扫描（`gitleaks` 互补）
- [ ] 自动修复建议（命中 high 时给出 "建议替换为环境变量引用"）
