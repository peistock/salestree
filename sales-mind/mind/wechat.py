"""
企业微信 API 封装
- 消息接收（XML 解析）
- 主动推送（文字、文件）
- 媒体文件下载（语音 AMR）
- 失败重试（指数退避）
"""
import os
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

CORPID = os.getenv("WECHAT_CORPID", "")
AGENTID = os.getenv("WECHAT_AGENTID", "")
SECRET = os.getenv("WECHAT_SECRET", "")
TOKEN = os.getenv("WECHAT_TOKEN", "")
AESKEY = os.getenv("WECHAT_AESKEY", "")

_token_cache = {"val": "", "exp": 0}


def get_access_token() -> str:
    """获取企微 access_token，带缓存"""
    global _token_cache
    now = time.time()
    if _token_cache["val"] and _token_cache["exp"] > now:
        return _token_cache["val"]

    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORPID}&corpsecret={SECRET}"
    try:
        r = requests.get(url, timeout=10).json()
        if r.get("errcode") != 0:
            logger.error(f"获取 token 失败: {r}")
            return ""
        _token_cache["val"] = r["access_token"]
        _token_cache["exp"] = now + 7000
        return _token_cache["val"]
    except Exception as e:
        logger.error(f"获取 token 异常: {e}")
        return ""


def verify_url(signature: str, timestamp: str, nonce: str, echostr: str) -> bool:
    """验证企微回调 URL"""
    tmp_list = [TOKEN, timestamp, nonce, echostr]
    tmp_list.sort()
    tmp_str = "".join(tmp_list).encode("utf-8")
    hashcode = hashlib.sha1(tmp_str).hexdigest()
    return hashcode == signature


def verify_and_decrypt_echostr(signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """验证签名并解密 GET 请求的 echostr"""
    from wechatpy.crypto import WeChatCrypto, PrpCrypto
    crypto = WeChatCrypto(TOKEN, AESKEY, CORPID)
    return crypto._check_signature(signature, timestamp, nonce, echostr, PrpCrypto)


def parse_message(xml_bytes: bytes) -> Dict[str, Any]:
    """解析企微 XML 消息"""
    try:
        root = ET.fromstring(xml_bytes)
        return {
            "to_user": _xml_text(root, "ToUserName"),
            "from_user": _xml_text(root, "FromUserName"),
            "create_time": int(_xml_text(root, "CreateTime", "0")),
            "msg_type": _xml_text(root, "MsgType"),
            "content": _xml_text(root, "Content"),
            "msg_id": _xml_text(root, "MsgId"),
            "media_id": _xml_text(root, "MediaId"),
            "format": _xml_text(root, "Format"),
            "agent_id": _xml_text(root, "AgentID"),
        }
    except Exception as e:
        logger.error(f"解析 XML 失败: {e}")
        return {}


def _xml_text(root, tag: str, default: str = "") -> str:
    node = root.find(tag)
    return node.text if node is not None else default


def decrypt_msg(xml_bytes: bytes, signature: str, timestamp: str, nonce: str) -> str:
    """解密企微加密消息，返回明文 XML 字符串"""
    from wechatpy.crypto import WeChatCrypto
    crypto = WeChatCrypto(TOKEN, AESKEY, CORPID)
    return crypto.decrypt_message(xml_bytes, signature, timestamp, nonce)


def push_text(user_id: str, text: str, max_retry: int = 3) -> Dict[str, Any]:
    """主动推送文字消息，带指数退避重试"""
    token = get_access_token()
    if not token:
        return {"err": "no_token"}

    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    payload = {
        "touser": user_id,
        "msgtype": "text",
        "agentid": int(AGENTID),
        "text": {"content": text[:2040]},  # 企微限制约 2048 字
        "safe": 0,
    }

    for attempt in range(max_retry):
        try:
            r = requests.post(url, json=payload, timeout=10)
            result = r.json()
            if result.get("errcode") == 0:
                return result
            # access_token 过期
            if result.get("errcode") == 42001:
                _token_cache["val"] = ""
                token = get_access_token()
                url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
                continue
            logger.warning(f"推送失败(attempt {attempt+1}): {result}")
        except Exception as e:
            logger.warning(f"推送异常(attempt {attempt+1}): {e}")

        if attempt < max_retry - 1:
            time.sleep(2 ** attempt)  # 1s, 2s, 4s

    return {"err": "push_failed", "attempts": max_retry}


def download_media(media_id: str, save_path: str) -> bool:
    """下载企微媒体文件（语音 AMR 等）"""
    token = get_access_token()
    if not token:
        return False

    url = f"https://qyapi.weixin.qq.com/cgi-bin/media/get?access_token={token}&media_id={media_id}"
    try:
        r = requests.get(url, timeout=30, stream=True)
        if r.status_code == 200:
            with open(save_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"媒体文件已下载: {save_path}")
            return True
        else:
            logger.error(f"下载媒体失败: {r.status_code}, {r.text}")
            return False
    except Exception as e:
        logger.error(f"下载媒体异常: {e}")
        return False


def push_file(user_id: str, media_id: str, title: str = "", max_retry: int = 3) -> Dict[str, Any]:
    """推送文件消息（用于发送 TTS 生成的 MP3）"""
    token = get_access_token()
    if not token:
        return {"err": "no_token"}

    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    payload = {
        "touser": user_id,
        "msgtype": "file",
        "agentid": int(AGENTID),
        "file": {"media_id": media_id},
        "safe": 0,
    }

    for attempt in range(max_retry):
        try:
            r = requests.post(url, json=payload, timeout=10)
            result = r.json()
            if result.get("errcode") == 0:
                return result
            if result.get("errcode") == 42001:
                _token_cache["val"] = ""
                token = get_access_token()
                url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
                continue
        except Exception as e:
            logger.warning(f"推送文件异常(attempt {attempt+1}): {e}")

        if attempt < max_retry - 1:
            time.sleep(2 ** attempt)

    return {"err": "push_file_failed"}


def upload_media(file_path: str, media_type: str = "file") -> Optional[str]:
    """上传媒体文件到企微，获取 media_id"""
    token = get_access_token()
    if not token:
        return None

    url = f"https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={token}&type={media_type}"
    try:
        with open(file_path, "rb") as f:
            files = {"media": (os.path.basename(file_path), f)}
            r = requests.post(url, files=files, timeout=30)
            result = r.json()
            if result.get("errcode") == 0:
                return result.get("media_id")
            logger.error(f"上传媒体失败: {result}")
    except Exception as e:
        logger.error(f"上传媒体异常: {e}")
    return None
