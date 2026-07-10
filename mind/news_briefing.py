"""
今日简报推送 —— 从 tophub.today/daily 抓取早报/晚报，生成销售同事友好的简报
"""
import os
import re
import logging
import requests
from datetime import datetime
from bs4 import BeautifulSoup, Tag
from typing import List, Tuple, Literal

from mind.llm_client import chat

logger = logging.getLogger(__name__)
MODEL_DAILY = os.getenv("MODEL_DAILY", "deepseek-chat")

# 排除过于垂直的领域（不适合销售场景）
_SKIP_KEYWORDS = {
    "水泥", "黑色系", "能化", "期货", "ETF", "期权", "铁矿石", "螺纹钢",
    "焦煤", "焦炭", "纯碱", "玻璃", "PVC", "甲醇", "原油", "沥青",
    "足球", "世界杯", "欧冠", "英超", "西甲", "球员", "转会", "NBA",
    "二次元", "动漫", "cosplay", "手办", "谷子", "电竞", "游戏",
    "币圈", "加密货币", "比特币", "以太坊", "挖矿", "DeFi",
}


def fetch_tophub_daily(limit: int = 30, period: Literal["morning", "evening", "all"] = "morning") -> List[Tuple[str, str]]:
    """
    抓取 tophub.today/daily 的早报/晚报列表。
    period: morning(早报) | evening(晚报) | all(全部)
    返回 [(来源, 标题), ...]
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        resp = requests.get(
            "https://tophub.today/daily",
            headers=headers,
            timeout=20,
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"抓取 tophub 失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    current_section = None
    items = []

    for elem in soup.descendants:
        if not isinstance(elem, Tag):
            continue
        cls = " ".join(elem.get("class", []))
        text = elem.get_text(strip=True)

        # 根据 header 切换区域
        if "header" in cls:
            if "早报聚合" in text:
                current_section = "morning"
            elif "晚报聚合" in text:
                current_section = "evening"
            elif "日报周刊" in text or "新闻联播" in text or "今日简报" in text:
                current_section = None
            continue

        # 按 period 筛选区域
        if period == "morning" and current_section != "morning":
            continue
        if period == "evening" and current_section != "evening":
            continue
        if period == "all" and current_section not in ("morning", "evening"):
            continue

        # 提取条目文本
        if "kt-t__item-text" in cls:
            if len(text) < 15 or len(text) > 120:
                continue
            # 排除垂直领域
            if any(kw in text for kw in _SKIP_KEYWORDS):
                continue
            # 排除纯数字/代码
            if re.match(r"^[\d\s]+$", text):
                continue

            # 找来源
            source = ""
            parent = elem.parent
            if isinstance(parent, Tag):
                header = parent.find_previous_sibling(class_=re.compile("kt-t__item-header|kt-t__item-section"))
                if header:
                    source = header.get_text(strip=True)

            items.append((source, text))

    # 去重
    seen = set()
    unique = []
    for source, text in items:
        if text not in seen:
            seen.add(text)
            unique.append((source, text))

    return unique[:limit]


def generate_briefing(news_items: List[Tuple[str, str]], period: Literal["morning", "evening"] = "morning") -> str:
    """
    用 LLM 把早报/晚报列表转成销售同事友好的简报。
    返回简报文本（已格式化）。
    """
    if not news_items:
        label = "早报" if period == "morning" else "晚报"
        return f"今天{label}还没更新，晚点我再看看～"

    today = datetime.now().strftime("%m月%d日")
    label = "早报" if period == "morning" else "晚报"
    news_text = "\n".join(f"{i+1}. [{s[:12]}] {t}" for i, (s, t) in enumerate(news_items))

    prompt = f"""今天是{today}，以下是从各大平台整理的今日{label}标题。请为销售同事生成一份简短的"今日{label}"，要求：

1. 语气专业、有信息量，像同事间分享资讯
2. 只挑 3-5 条最重要、对商业/行业/客户最有用的（科技、消费、互联网、政策、市场动态）
3. 每条用一句话概括，不要太长
4. 开头说"各位同事，今天{label}来啦～"
5. 结尾加一句轻松的提醒（比如"今天外面风大，出门多穿点"之类的，如果没有天气信息就说"今天的事就这些，有事儿随时找我～"）
6. 总字数控制在 200 字以内，合并成一条消息
7. 不要出现"{label}标题如下""根据以下信息"这类机器人口吻

原始标题：
{news_text}

请直接输出简报内容："""

    try:
        return chat(
            system=f"你是销售智能助手销销，正在给销售同事播报今日{label}。",
            user_prompt=prompt,
            model=MODEL_DAILY,
            max_tokens=600,
            temperature=0.5,
        )
    except Exception as e:
        logger.error(f"生成简报失败: {e}")
        # 降级：直接拼接
        lines = [f"各位同事，今天{label}来啦～"] + [
            f"• {t[:40]}" for _, t in news_items[:5]
        ] + ["今天的事就这些，有事儿随时找我～"]
        return "\n".join(lines)


def get_daily_briefing(period: Literal["morning", "evening"] = "morning") -> str:
    """一键获取今日简报"""
    items = fetch_tophub_daily(limit=25, period=period)
    label = "早报" if period == "morning" else "晚报"
    logger.info(f"抓取到 {len(items)} 条{label}")
    return generate_briefing(items, period=period)


if __name__ == "__main__":
    # 本地测试
    print("=== 早报 ===")
    print(get_daily_briefing("morning"))
    print("\n=== 晚报 ===")
    print(get_daily_briefing("evening"))
