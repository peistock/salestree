"""
API key 加密工具
使用 AES-256-GCM，密文格式：base64(nonce(12B) + ciphertext + tag(16B))
与 server/src/utils/encryption.ts 互通。
"""
import base64
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_key() -> Optional[bytes]:
    """从环境变量获取 32 字节密钥；未设置返回 None。"""
    import os

    key = os.getenv("USER_LLM_ENCRYPTION_KEY")
    if not key:
        return None
    return hashlib.sha256(key.encode("utf-8")).digest()


def encrypt_api_key(plaintext: str) -> str:
    """加密 API key；未设置密钥时直接返回明文（不安全，仅兼容）。"""
    key = _get_key()
    if key is None:
        return plaintext
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext_with_tag).decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    """解密 API key；未设置密钥时按明文返回。"""
    key = _get_key()
    if key is None:
        return ciphertext
    try:
        data = base64.b64decode(ciphertext.encode("ascii"))
        nonce = data[:12]
        ciphertext_with_tag = data[12:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        return plaintext.decode("utf-8")
    except Exception as e:
        raise ValueError(f"API key 解密失败: {e}")


def mask_api_key(key: str) -> str:
    """显示首尾各 4 位，中间用 * 替代。"""
    if not key or len(key) <= 8:
        return key
    return key[:4] + "****" + key[-4:]
