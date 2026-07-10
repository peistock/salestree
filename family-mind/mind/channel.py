"""
消息通道抽象层
解耦 Agent 处理逻辑与具体消息通道实现（企微 / 公众号 / 未来其他）
"""
import os
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class MessageChannel(ABC):
    """消息通道抽象基类。Agent 处理完消息后，通过此接口回复用户。"""

    @abstractmethod
    def send_text(self, user_id: str, text: str) -> Dict[str, Any]:
        """发送文本消息"""

    def send_file(self, user_id: str, file_path: str, title: str = "") -> Dict[str, Any]:
        """发送文件（可选实现，默认返回失败）"""
        return {"err": "not_implemented", "msg": f"{self.name} 暂不支持发文件"}

    @property
    @abstractmethod
    def name(self) -> str:
        """通道标识名"""


class WeComChannel(MessageChannel):
    """企业微信通道 —— 复用现有 mind.wechat 模块"""

    @property
    def name(self) -> str:
        return "wecom"

    def send_text(self, user_id: str, text: str) -> Dict[str, Any]:
        from mind.wechat import push_text
        return push_text(user_id, text)

    def send_file(self, user_id: str, file_path: str, title: str = "") -> Dict[str, Any]:
        from mind.wechat import upload_media, push_file
        media_id = upload_media(file_path, media_type="file")
        if media_id:
            return push_file(user_id, media_id, title=title or Path(file_path).name)
        return {"err": "upload_failed"}


class MpChannel(MessageChannel):
    """
    微信公众号通道
    - 发送文本消息（通过客服消息接口，48 小时窗口内）
    - 发文件暂不支持
    """

    def __init__(self):
        self._reverse_map = {}  # family_id -> openid
        self._init_mapping()

    def _init_mapping(self):
        """初始化 openid <-> family_id 双向映射（包含 .env + 自动映射文件）"""
        mapping = {}
        # 1. 从 .env 加载
        raw = os.getenv("MP_USERS", "{}")
        try:
            mapping.update(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning(f"MP_USERS 格式错误: {raw}")
        # 2. 从自动映射文件加载
        auto_file = Path(os.getenv("DATA_DIR", "./data")) / "mp_auto_users.json"
        if auto_file.exists():
            try:
                data = json.loads(auto_file.read_text(encoding="utf-8"))
                mapping.update(data.get("mapping", {}))
            except Exception as e:
                logger.warning(f"加载 mp_auto_users 失败: {e}")
        self._reverse_map = {v: k for k, v in mapping.items()}

    @property
    def name(self) -> str:
        return "mp"

    def _openid_for(self, family_id: str) -> Optional[str]:
        """family member ID -> openid"""
        openid = self._reverse_map.get(family_id)
        if not openid:
            logger.warning(f"公众号找不到 family_id={family_id} 对应的 openid，请检查 MP_USERS 配置")
        return openid

    def send_text(self, user_id: str, text: str) -> Dict[str, Any]:
        openid = self._openid_for(user_id)
        if not openid:
            return {"err": "no_openid_mapping", "user_id": user_id}

        from mind.mp_client import send_custom_message
        # 长消息截断（公众号客服消息单条限制约 2048 字）
        chunks = []
        for i in range(0, len(text), 2000):
            chunks.append(text[i:i + 2000])

        results = []
        for chunk in chunks:
            result = send_custom_message(openid, chunk)
            results.append(result)
            if result.get("errcode", 0) != 0:
                logger.warning(f"公众号发送文本失败: {result}")

        return {"results": results, "chunks": len(chunks)}

    def send_file(self, user_id: str, file_path: str, title: str = "") -> Dict[str, Any]:
        """公众号客服消息暂不支持发文件"""
        openid = self._openid_for(user_id)
        if not openid:
            return {"err": "no_openid_mapping", "user_id": user_id}

        logger.info(f"公众号发文件暂不支持，跳过: {file_path}")
        self.send_text(user_id, f"[文件] {title or Path(file_path).name}\n（公众号通道暂不支持自动发文件，请通过企业微信查看）")
        return {"err": "not_yet_supported", "msg": "公众号客服消息暂不支持发文件"}


def get_channel_for(user_id: str, default: str = "wecom") -> MessageChannel:
    """
    根据用户 ID 判断应该走哪个通道。
    如果 user_id 在 MP_USERS 的 value 中，返回 MpChannel。
    否则返回 WeComChannel。
    """
    raw = os.getenv("MP_USERS", "{}")
    try:
        mapping = json.loads(raw)  # openid -> family_id
        mp_ids = set(mapping.values())
        if user_id in mp_ids:
            return MpChannel()
    except json.JSONDecodeError:
        pass
    return WeComChannel()
