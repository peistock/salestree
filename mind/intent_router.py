"""
意图路由 —— 简单请求 vs 复杂请求分流

L1 关键词匹配（< 1ms）
L2 向量匹配（预留）
L3 LLM 兜底（预留）

当前实现 L1 规则覆盖 销销 高频简单场景。
"""

import re
from typing import Tuple

# 立即判为简单意图的模式（无歧义）
_IMMEDIATE_SIMPLE_PATTERNS = {
    "greeting": [
        r"^(早上好|中午好|晚上好|晚安|在吗|在不在|你好|您好|嗨|哈喽|在忙吗|早|早安)",
        r"^(最近好吗|还好吗|吃了吗|休息好了吗)",
    ],
    "identity": [
        r"(你是谁|你叫什么|你.*?(叫|是).*?(谁|什么)|你能.*?(做|干)什么|你是.*?(谁|什么|机器人|AI))",
        r"(介绍|说说|讲讲).*?(自己|你|你的功能)",
        r"(怎么称呼|该怎么叫你|叫你什么)",
    ],
    "gratitude": [
        r"^(谢谢|感谢|多谢|辛苦了|真好|真棒|不错|可以|好的|知道了|明白|行|中|没问题|好的呢|嗯|嗯嗯|哦|好的好的)",
    ],
}

# 需结合话题判断的简单意图模式（避免与复杂研究查询冲突）
_CONDITIONAL_SIMPLE_PATTERNS = {
    "weather": [
        r"(天气|气温|温度|下雨|下雪|刮风|雾霾|空气质量|紫外线|湿度).*?(吗|怎么样|如何|多少度)",
        r"(今天|明天|后天|这周|周末|下周|这几天).*?(天气|气温|下雨|下雪|冷不冷|热不热|降温|升温)",
        r"(带伞|穿.*?(衣服|外套|毛衣|羽绒服)|出门.*?(穿|带)|冷不冷|热不热|几度)",
    ],
    "schedule_check": [
        r"(几点|现在.*?(时间|几点)|时间)",
        r"(今天|明天|这周|下周|后天|这几天).*?(安排|日程|计划|什么日子|几号|星期几|周几)",
        r"(什么时候|几点钟|哪天|几号).*?(会议|拜访|电话|跟进|客户|活动|聚会|吃饭)",
        r"(最近|接下来).*?(有什么|要做什么|安排|活动|聚会|聚餐)",
    ],
    "reminder": [
        r"(提醒|通知|告诉|喊|叫).*?(跟进|客户|会议|电话|拜访|提交|发送|回复|出门|吃饭|休息)",
        r"(记得|别忘了|别忘记|记着).*?(跟进|客户|会议|电话|拜访|提交|发送|回复|出门|吃饭|休息)",
        r"(定个|设个|加个).*?(提醒|闹钟|通知)",
    ],
    "news": [
        r"(有什么|最近|今天|这几天).*?(新闻|新鲜事|大事|消息|热点|热闹|八卦)",
        r"(今天|昨天|这几天).*?(发生|出了|有).*?(什么|啥|大事)",
        r"(看看|说说|讲讲).*?(新闻|消息|新鲜事)",
    ],
    "joke": [
        r"(讲个|说个|来段|来首|来一个).*?(笑话|故事|相声|小品|段子|新闻|消息)",
    ],
}

# 简单意图模式（兼容旧引用）
_SIMPLE_INTENT_PATTERNS = {**_IMMEDIATE_SIMPLE_PATTERNS, **_CONDITIONAL_SIMPLE_PATTERNS}

# 复杂请求信号（出现即判定为复杂）
_COMPLEX_SIGNALS = [
    r"(分析|研究|报告|调研|深度|详细|全面|系统|完整)",
    r"(对比|比较|区别|优劣|哪个好|怎么选|选哪个|排名)",
    r"(生成|制作|创建|导出|写.*?(报告|分析|总结|文档|文章|文案))",
    r"(pdf|文档|文件|表格|图表|可视化|ppt|幻灯片)",
    r"(为什么|怎么回事|原因|影响|趋势|预测|展望|前景)",
    r"(查.*?(然后|接着|再|之后|同时).*?(查|写|分析|生成|对比))",
    r"(至少|不少于|超过|万字|3000|5000|10000|3千|5千|1万)",
    r"(横纵|横向|纵向|多维度|全方位|系统性)",
    r"(股票|基金|投资|理财|行情|走势|k线|财报|估值|pe|pb)",
    r"(品牌|市场|营销|策略|渠道|竞品|消费者|用户画像)",
    r"(帮我|给我|想|要).*?(做|写|生成|整理|总结|分析|发).*?(一个|一份|一篇|一下)",
    r"(发一下|发一份|发一个|给一份|给一下).*?(晚报|早报|简报|新闻|报告|消息)",
    r"(第一步|先.*?(然后|接着)|流程|步骤|怎么做|如何)",
    r"(晚报|早报|简报|日报|周报|月报|报告)",
]


# 动作词（用于兜底判断）
_ACTION_WORDS = {"查", "搜", "找", "看", "问", "读", "查下", "查一查", "看看", "搜一下", "找一下", "问问", "查一下", "查查看"}

# 复杂话题：出现这些词的动作查询，需要多轮工具/分析
_COMPLEX_TOPICS = {
    "融资", "财报", "业绩", "年报", "季报", "半年报", "营收", "利润", "净利润",
    "公司", "企业", "品牌", "竞品", "竞争对手", "对手",
    "市场", "行业", "赛道", "趋势", "前景", "格局", "份额",
    "高管", "人事", "团队", "组织架构", "创始人", "CEO", "总监", "负责人",
    "投资", "并购", "IPO", "上市", "估值", "市值", "PE", "PB",
    "策略", "营销", "渠道", "投放", "广告", "用户画像", "消费者",
    "研究", "分析", "调研", "报告", "深度", "全面", "系统",
}

# 简单话题：动作查询 + 这些词 → 一轮工具即可
_SIMPLE_TOPICS = {
    "股价", "天气", "气温", "温度", "时间", "几点", "日程", "安排", "新闻",
}


def classify_intent(text: str) -> Tuple[str, float]:
    """
    意图分类：返回 (intent_type, confidence)
    intent_type: simple | complex | unknown

    设计原则：
    - 无歧义简单意图（问候、身份、感谢）立即判 simple
    - 复杂信号/复杂话题优先判 complex
    - 条件简单模式（天气、日程、新闻等）在无复杂话题时判 simple
    - 去掉长度启发式，避免"20 字魔咒"
    - 兜底默认 complex：宁可多花一轮，也不少搜漏答
    """
    if not text:
        return ("unknown", 0.0)

    text = text.strip()

    # 1. 无歧义简单意图
    for category, patterns in _IMMEDIATE_SIMPLE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return ("simple", 0.95)

    # 2. 明确复杂请求信号
    for pattern in _COMPLEX_SIGNALS:
        if re.search(pattern, text, re.IGNORECASE):
            return ("complex", 0.9)

    # 3. 动作查询 + 复杂话题 → complex（避免把研究类查询误判为简单）
    has_action = any(w in text for w in _ACTION_WORDS)
    if has_action and any(topic in text for topic in _COMPLEX_TOPICS):
        return ("complex", 0.85)

    # 4. 条件简单意图（天气、日程、提醒、新闻、笑话）
    for category, patterns in _CONDITIONAL_SIMPLE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return ("simple", 0.9)

    # 5. 动作查询 + 简单话题 → simple
    if has_action and any(topic in text for topic in _SIMPLE_TOPICS):
        return ("simple", 0.8)

    # 6. 默认复杂
    return ("complex", 0.6)


def get_intent_label(text: str) -> str:
    """返回可读意图标签（用于日志和调试）"""
    intent, conf = classify_intent(text)
    return f"{intent}({conf:.0%})"
