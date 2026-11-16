"""
backend/services/encryption.py

AES-256-GCM 加密服务

用于加密/解密敏感数据（如 API Key），确保：
- 数据在数据库中加密存储
- 支持环境变量配置主密钥
- 提供内存清除功能（示意性）

对应数据流节点：J（LLM Judge）
"""

import os
import base64
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptionService:
    """AES-256-GCM 加密服务"""

    def __init__(self, master_key: Optional[bytes] = None):
        """
        初始化加密服务

        Args:
            master_key: 32 字节主密钥，如果未提供则从环境变量读取
        """
        if master_key:
            self.master_key = master_key
        else:
            # 从环境变量读取（base64 编码）
            key_b64 = os.environ.get("ENCRYPTION_MASTER_KEY", "")
            if not key_b64:
                # 开发模式：生成随机密钥（生产环境必须配置）
                self.master_key = AESGCM.generate_key(bit_length=256)
            else:
                self.master_key = base64.b64decode(key_b64)

    def encrypt(self, plaintext: str) -> str:
        """
        加密字符串

        Returns:
            base64(nonce + ciphertext + tag)
        """
        aesgcm = AESGCM(self.master_key)
        nonce = os.urandom(12)  # 96-bit nonce

        ciphertext = aesgcm.encrypt(
            nonce,
            plaintext.encode("utf-8"),
            None,  # associated_data
        )

        # nonce + ciphertext (包含 tag)
        encrypted = nonce + ciphertext
        return base64.b64encode(encrypted).decode("utf-8")

    def decrypt(self, encrypted: str) -> str:
        """
        解密字符串

        Args:
            encrypted: base64(nonce + ciphertext + tag)

        Returns:
            解密后的明文
        """
        aesgcm = AESGCM(self.master_key)
        encrypted_bytes = base64.b64decode(encrypted)

        # 提取 nonce (前 12 字节)
        nonce = encrypted_bytes[:12]
        ciphertext = encrypted_bytes[12:]

        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")

    @staticmethod
    def secure_clear(s: str) -> None:
        """
        尝试清除字符串（Python 中有限，仅作示意）

        注意：Python 字符串不可变，无法真正清除。
        生产环境应使用 bytearray 或专门的安全库。
        """
        # 仅作示意，实际无法清除 Python 字符串
        del s
