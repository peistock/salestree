"""
微信公众号 API 封装
- 消息接收（XML 解析）
- 被动回复（XML 构造）
- 主动推送（客服消息）
- access_token 管理
"""
import os
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

MP_APPID = os.getenv("MP_APPID", "")
MP_APPSECRET = os.getenv("MP_APPSECRET", "")
MP_TOKEN = os.getenv("MP_TOKEN", "")

_token_cache = {"val": "", "exp": 0}


def get_access_token() -> str:
    """获取公众号 access_token，带缓存"""
    global _token_cache
    now = time.time()
    if _token_cache["val"] and _token_cache["exp"] > now:
        return _token_cache["val"]

    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={MP_APPID}&secret={MP_APPSECRET}"
    try:
        r = requests.get(url, timeout=10).json()
        if "access_token" not in r:
            logger.error(f"获取 token 失败: {r}")
            return ""
        _token_cache["val"] = r["access_token"]
        _token_cache["exp"] = now + 7000
        return _token_cache["val"]
    except Exception as e:
        logger.error(f"获取 token 异常: {e}")
        return ""


def verify_signature(signature: str, timestamp: str, nonce: str) -> bool:
    """验证公众号回调签名"""
    tmp_list = [MP_TOKEN, timestamp, nonce]
    tmp_list.sort()
    tmp_str = "".join(tmp_list).encode("utf-8")
    hashcode = hashlib.sha1(tmp_str).hexdigest()
    return hashcode == signature


def parse_message(xml_bytes: bytes) -> Dict[str, Any]:
    """解析公众号 XML 消息"""
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
            "event": _xml_text(root, "Event"),
            "event_key": _xml_text(root, "EventKey"),
        }
    except Exception as e:
        logger.error(f"解析 XML 失败: {e}")
        return {}


def _xml_text(root, tag: str, default: str = "") -> str:
    node = root.find(tag)
    return node.text if node is not None else default


def build_reply_xml(to_user: str, from_user: str, content: str) -> str:
    """构造被动回复 XML"""
    return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""


def send_custom_message(openid: str, text: str) -> Dict[str, Any]:
    """发送客服消息（需在用户发消息 48 小时内）"""
    token = get_access_token()
    if not token:
        return {"errcode": -1, "errmsg": "no_token"}

    url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}"
    payload = {
        "touser": openid,
        "msgtype": "text",
        "text": {"content": text[:2040]},
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        result = r.json()
        if result.get("errcode") == 0:
            return result
        # token 过期
        if result.get("errcode") == 40001:
            _token_cache["val"] = ""
            token = get_access_token()
            url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}"
            r = requests.post(url, json=payload, timeout=10)
            return r.json()
        logger.warning(f"公众号客服消息发送失败: {result}")
        return result
    except Exception as e:
        logger.error(f"公众号客服消息异常: {e}")
        return {"errcode": -1, "errmsg": str(e)}


def get_user_info(openid: str) -> Dict[str, Any]:
    """获取用户基本信息（昵称、头像等）"""
    token = get_access_token()
    if not token:
        return {}
    url = f"https://api.weixin.qq.com/cgi-bin/user/info?access_token={token}&openid={openid}&lang=zh_CN"
    try:
        r = requests.get(url, timeout=10)
        return r.json()
    except Exception as e:
        logger.error(f"获取用户信息失败: {e}")
        return {}
