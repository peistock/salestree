"""
消息清理工具（Hermes message_sanitization.py 精简适配）
在 AgentLoop 每轮调用 LLM 前执行，防止脏数据导致 API 异常。
"""
import json
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _strip_surrogates(text: str) -> str:
    """移除 UTF-16 surrogate 字符，避免 JSON 序列化失败"""
    if not isinstance(text, str):
        return text
    # 移除孤立的 surrogate code points (U+D800-U+DFFF)
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "ignore")


def _strip_images_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    移除消息中的图片内容，避免超长 base64 撑爆上下文。
    保留文本部分，并提示图片已被移除。
    """
    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = [
                item.get("text", "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if text_parts:
                new_text = "\n".join(text_parts)
                cleaned.append({**msg, "content": f"{new_text}\n[图片内容已移除]"})
            else:
                cleaned.append(msg)
        else:
            cleaned.append(msg)
    return cleaned


def _repair_tool_call_arguments(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    修复 tool_call 参数中的常见 JSON 问题：
    - 参数为空字符串 → 改为 "{}"
    - 参数是 dict → 序列化为 JSON 字符串
    - 包含控制字符 / surrogate → 清理
    """
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            continue
        for tc in tool_calls:
            func = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
            if not func:
                continue
            args = func.get("arguments") if isinstance(func, dict) else getattr(func, "arguments", None)
            if args is None or args == "":
                func["arguments"] = "{}"
            elif isinstance(args, dict):
                try:
                    func["arguments"] = json.dumps(args, ensure_ascii=False)
                except Exception:
                    func["arguments"] = "{}"
            elif isinstance(args, str):
                try:
                    # 验证并清理
                    parsed = json.loads(args)
                    func["arguments"] = json.dumps(parsed, ensure_ascii=False)
                except json.JSONDecodeError:
                    # 尝试修复常见错误：多余的逗号、单引号等
                    try:
                        fixed = args.replace("'", '"').rstrip(",")
                        parsed = json.loads(fixed)
                        func["arguments"] = json.dumps(parsed, ensure_ascii=False)
                    except Exception:
                        logger.warning(f"无法修复 tool_call 参数，已清空: {args[:200]!r}")
                        func["arguments"] = "{}"
    return messages


def _strip_control_chars(text: str) -> str:
    """移除除换行、制表符外的控制字符"""
    if not isinstance(text, str):
        return text
    return "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)


def sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    统一入口：清理消息列表中的非 ASCII 异常、surrogate、图片、工具参数等。
    返回新的消息列表，不修改原列表（浅拷贝）。
    """
    messages = [dict(m) for m in messages]
    messages = _strip_images_from_messages(messages)

    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            content = _strip_surrogates(content)
            content = _strip_control_chars(content)
            msg["content"] = content

        # 清理 tool result 内容
        if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
            msg["content"] = _strip_surrogates(_strip_control_chars(msg["content"]))

    messages = _repair_tool_call_arguments(messages)
    return messages
