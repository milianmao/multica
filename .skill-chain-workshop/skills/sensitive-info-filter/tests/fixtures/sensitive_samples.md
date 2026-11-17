# 敏感信息样本

本文件用于测试过滤器是否能识别各种敏感信息。

## 凭证示例

```bash
# OpenAI API Key
export OPENAI_API_KEY=sk-prod1234567890abcdefghijklmnopqrstuvwxyz

# GitHub Token
GH_TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

# AWS Access Key
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
```

## 私钥块

```
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF5PBbGPhqUg
-----END RSA PRIVATE KEY-----
```

## PII

- 联系电话：13812345678
- 邮箱：john.doe@example.com
- 身份证：110101199003078888

## 银行卡

- Visa 测试卡：4111111111111111
- Mastercard 测试卡：5500000000000004

## 内网 IP

- 数据库：192.168.1.100
- 缓存：10.0.0.5
