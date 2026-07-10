"""
上下文压缩器（v3 — 本地 Gemma 摘要）

核心逻辑：
1. 按"完整回合"分组（assistant tool_call + 对应 tool results 不可分割）
2. 超过阈值时，压缩中间轮次，保留头和尾
3. 用本地 LM Studio 的 Gemma 4 26B 生成摘要（~4秒）
4. 如果本地模型不可用，fallback 到代码清单（<1ms）
5. 压缩后重新注入 todo 列表（防止 LLM 忘记任务）
"""
import logging
import os
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# 估算阈值：中文字符≈1 token，英文≈0.3 token，取保守值
DEFAULT_TARGET_TOKENS = 8000
# 尾部保护：最近 N 条消息不动（按条数，实际会对齐到完整回合）
PROTECT_LAST_N = 4
# 头部保护：system + 前 2 轮（按条数，实际会对齐到完整回合）
PROTECT_HEAD_N = 3

# 本地模型配置（LM Studio）
LOCAL_BASE_URL = os.getenv("LOCAL_MODEL_URL", "http://127.0.0.1:1234/v1")
LOCAL_SUMMARY_MODEL = os.getenv("LOCAL_MODEL_NAME", "gemma-4-26b-a4b-it-ud")


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文按字数，英文按空格分词"""
    if not text:
        return 0
    return int(len(text) * 0.6)


def _estimate_messages_tokens(messages: List[Dict]) -> int:
    """估算 messages 数组的总 token 数"""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                total += _estimate_tokens(tc.get("function", {}).get("arguments", ""))
        total += _estimate_tokens(content)
    return total


def _find_turn_boundaries(messages: List[Dict]) -> List[tuple]:
    """
    把消息数组按"完整回合"划分边界。
    一个回合 = assistant message（如果带 tool_calls，则包含后面所有对应的 tool messages）
    返回 [(start_idx, end_idx), ...]，end_idx 是排他的
    """
    boundaries = []
    i = 0
    n = len(messages)
    while i < n:
        start = i
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tool_call_ids = {tc.get("id") for tc in msg["tool_calls"]}
            i += 1
            while i < n and messages[i].get("role") == "tool":
                if messages[i].get("tool_call_id") in tool_call_ids:
                    i += 1
                else:
                    break
        else:
            i += 1
        boundaries.append((start, i))
    return boundaries


def _generate_turn_manifest(messages: List[Dict], start: int, end: int) -> str:
    """
    用代码生成中间轮次的工具调用清单（本地模型不可用时 fallback）。
    """
    lines = []
    for i in range(start, end):
        msg = messages[i]
        role = msg.get("role", "unknown")

        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                name = tc.get("function", {}).get("name", "?")
                args = tc.get("function", {}).get("arguments", "")
                args_short = args[:200] + "..." if len(args) > 200 else args
                lines.append(f"[Turn {i}] → 调用 {name}({args_short})")

        elif role == "tool":
            content = msg.get("content", "") or ""
            if len(content) > 400:
                content = content[:400] + "..."
            lines.append(f"         结果：{content}")

        else:
            content = msg.get("content", "") or ""
            if len(content) > 300:
                content = content[:300] + "..."
            lines.append(f"[Turn {i} - {role}] {content}")

    if not lines:
        return "[中间轮次无有效内容]"

    return "\n".join(lines)


def _summarize_with_local_llm(content_to_summarize: str) -> Optional[str]:
    """
    用本地 LM Studio 的 Gemma 4 26B 生成摘要。
    如果本地模型不可用，返回 None，由调用方 fallback 到代码清单。
    """
    try:
        from openai import OpenAI

        client = OpenAI(base_url=LOCAL_BASE_URL, api_key=os.getenv("LLM_API_KEY", "not-needed"))

        summary_prompt = f"""请对以下对话历史进行简要摘要。这些是被压缩的中间轮次，摘要将替代它们保留在上下文中。

需要包含：
1. 已执行了哪些工具调用（搜索、浏览、文件操作等）
2. 获得的关键信息和结果
3. 当前进展和未完成的任务

要求：
- 纯事实摘要，不要加评价
- 保留关键数据、文件名、URL、数值
- 控制在 300 字以内

---
{content_to_summarize}
---

请直接输出摘要，不要加标题或前缀。"""

        resp = client.chat.completions.create(
            model=LOCAL_SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": "你是一个上下文摘要助手，负责把过长的对话历史压缩成简要摘要。"},
                {"role": "user", "content": summary_prompt},
            ],
            max_tokens=600,
            temperature=0.3,
        )
        summary = resp.choices[0].message.content or ""
        return summary.strip()

    except Exception as e:
        logger.warning(f"本地模型摘要失败: {e}")
        return None


def compress_messages(
    messages: List[Dict],
    llm_chat_fn=None,  # 保留兼容旧接口，但不再使用
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    todo_injection: Optional[str] = None,
) -> List[Dict]:
    """
    压缩 messages 数组（按完整回合压缩，不破坏 tool_call/tool 配对）。

    Args:
        messages: 当前对话历史
        llm_chat_fn: 已废弃，保留兼容旧代码
        target_tokens: 触发压缩的 token 阈值
        todo_injection: 如果有活跃的 todo 列表，压缩后重新注入

    Returns:
        压缩后的 messages（如果不需要压缩则返回原列表）
    """
    total_tokens = _estimate_messages_tokens(messages)
    if total_tokens <= target_tokens:
        return messages

    n = len(messages)
    turns = _find_turn_boundaries(messages)

    # 按回合数确定保护边界
    head_turn_idx = 0
    msg_count = 0
    for idx, (s, e) in enumerate(turns):
        msg_count += (e - s)
        if msg_count >= PROTECT_HEAD_N:
            head_turn_idx = idx
            break
    else:
        head_turn_idx = len(turns) - 1

    tail_turn_idx = len(turns) - 1
    msg_count = 0
    for idx in range(len(turns) - 1, -1, -1):
        s, e = turns[idx]
        msg_count += (e - s)
        if msg_count >= PROTECT_LAST_N:
            tail_turn_idx = idx
            break

    if head_turn_idx >= tail_turn_idx:
        return messages

    head_end_msg_idx = turns[head_turn_idx][1]
    tail_start_msg_idx = turns[tail_turn_idx][0]

    if head_end_msg_idx >= tail_start_msg_idx:
        return messages

    logger.info(f"上下文压缩触发: total_tokens≈{total_tokens}, messages={n}, "
                f"turns={len(turns)}, compress_region=[{head_end_msg_idx}:{tail_start_msg_idx}]")

    # 提取要压缩的内容
    content_to_summarize = _generate_turn_manifest(messages, head_end_msg_idx, tail_start_msg_idx)

    # 优先用本地 Gemma 生成摘要（~4秒），失败则 fallback 到代码清单（<1ms）
    summary = _summarize_with_local_llm(content_to_summarize)
    if summary:
        logger.info("上下文摘要由本地 Gemma 生成")
    else:
        summary = f"[上下文已压缩，中间轮次摘要如下]\n{content_to_summarize}"
        logger.info("本地 Gemma 不可用，fallback 到代码清单")

    # 构建压缩后的 messages
    compressed = []

    # 1. 头部（保留）
    for i in range(head_end_msg_idx):
        compressed.append(messages[i])

    # 2. 摘要（替换中间区域）
    compressed.append({"role": "user", "content": f"[上下文摘要] {summary}"})

    # 3. 如果有 todo 注入，加在摘要后面
    if todo_injection:
        compressed.append({"role": "user", "content": todo_injection})

    # 4. 尾部（保留最近 N 轮）
    for i in range(tail_start_msg_idx, n):
        compressed.append(messages[i])

    new_tokens = _estimate_messages_tokens(compressed)
    logger.info(f"上下文压缩完成: {n}轮 -> {len(compressed)}轮, "
                f"tokens {total_tokens} -> {new_tokens}")

    return compressed
