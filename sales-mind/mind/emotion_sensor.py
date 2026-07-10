"""
情绪感知模块（Phase 2.x）
- 文字层情绪检测：关键词 + 对话行为模式
- 情绪日志记录
- 触发阈值后静默通知夫妻
"""
import os
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from mind.memory import get_conn
from mind.wechat import push_text

logger = logging.getLogger(__name__)

# ========== 情绪词典 ==========
# 关键词 → 情绪标签 + 权重
EMOTION_KEYWORDS = {
    # 负面情绪（权重高）
    "没意思": ("low_mood", 3),
    "无聊": ("low_mood", 2),
    "烦": ("irritated", 2),
    "烦死了": ("irritated", 3),
    "累": ("tired", 2),
    "好累": ("tired", 3),
    "睡不着": ("insomnia", 3),
    "失眠": ("insomnia", 3),
    "疼": ("pain", 3),
    "痛": ("pain", 3),
    "难受": ("unwell", 3),
    "不舒服": ("unwell", 2),
    "担心": ("anxious", 2),
    "害怕": ("anxious", 3),
    "孤独": ("lonely", 3),
    "想儿子": ("lonely", 2),
    "想孙子": ("lonely", 2),
    "老了": ("aging_anxiety", 2),
    "没用": ("low_self_worth", 3),
    "拖后腿": ("low_self_worth", 3),

    # 积极情绪（用于平衡）
    "开心": ("happy", 2),
    "高兴": ("happy", 2),
    "不错": ("content", 1),
    "很好": ("content", 2),
    "舒服": ("content", 2),
    "精神": ("energetic", 2),
}

# 行为模式阈值
BEHAVIOR_THRESHOLDS = {
    "low_mood_3day": 2,       # 3 天内低情绪关键词出现 2 次以上
    "insomnia_streak": 2,     # 连续 2 天提到睡眠问题
    "anxious_3day": 2,        # 3 天内焦虑关键词出现 2 次以上
    "message_count_drop": 0.3,  # 消息量下降 70%（对比前一周均值）
    "response_delay": 300,    # 回复间隔超过 5 分钟（企微场景下不太适用，保留字段）
}

# 夫妻用户 ID（从 user_profiles 中 role='夫妻' 动态获取）

def _get_couple_users() -> List[str]:
    """获取夫妻用户的 user_id"""
    conn = get_conn()
    users = []
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id FROM user_profiles WHERE role='夫妻'")
            users = [r[0] for r in c.fetchall()]
    except Exception as e:
        logger.error(f"获取夫妻用户失败: {e}")
    finally:
        conn.close()
    return users


def detect_emotion(text: str) -> List[Tuple[str, str, int]]:
    """
    从文本中检测情绪信号
    返回: [(关键词, 情绪标签, 权重), ...]
    """
    found = []
    # 优先匹配长词（避免"烦死了"被拆成"烦"）
    sorted_keywords = sorted(EMOTION_KEYWORDS.keys(), key=len, reverse=True)
    remaining = text
    for kw in sorted_keywords:
        if kw in remaining:
            emotion, weight = EMOTION_KEYWORDS[kw]
            found.append((kw, emotion, weight))
            remaining = remaining.replace(kw, "", 1)  # 只替换一次
    return found


def log_emotion(user_id: str, text: str, emotions: List[Tuple[str, str, int]]):
    """记录情绪日志到数据库"""
    if not emotions:
        return

    emotion_str = ", ".join(f"{kw}:{label}({w})" for kw, label, w in emotions)
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO emotion_logs (user_id, source_text, emotions, created_at)
                VALUES (%s, %s, %s, NOW())
                """,
                (user_id, text[:500], emotion_str)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"记录情绪日志失败: {e}")
    finally:
        conn.close()


def check_alert_threshold(user_id: str) -> Optional[Dict]:
    """
    检查是否触发情绪预警阈值
    返回预警信息字典，或 None
    """
    conn = get_conn()
    try:
        # 检查最近 3 天的情绪日志
        with conn.cursor() as c:
            c.execute(
                """
                SELECT emotions FROM emotion_logs
                WHERE user_id=%s AND created_at > NOW() - INTERVAL '3 days'
                ORDER BY created_at DESC
                """,
                (user_id,)
            )
            rows = c.fetchall()

        if not rows:
            return None

        # 统计各情绪标签出现次数
        emotion_counts = {}
        for (emotions_str,) in rows:
            for part in emotions_str.split(", "):
                if ":" in part:
                    label = part.split(":")[1].split("(")[0]
                    emotion_counts[label] = emotion_counts.get(label, 0) + 1

        alerts = []
        # 低情绪预警
        if emotion_counts.get("low_mood", 0) >= BEHAVIOR_THRESHOLDS["low_mood_3day"]:
            alerts.append(f"近3天出现{emotion_counts['low_mood']}次情绪低落信号")
        # 失眠预警
        if emotion_counts.get("insomnia", 0) >= BEHAVIOR_THRESHOLDS["insomnia_streak"]:
            alerts.append(f"近3天出现{emotion_counts['insomnia']}次睡眠问题信号")
        # 焦虑预警
        if emotion_counts.get("anxious", 0) >= BEHAVIOR_THRESHOLDS["anxious_3day"]:
            alerts.append(f"近3天出现{emotion_counts['anxious']}次焦虑信号")
        # 身体不适预警
        pain_count = emotion_counts.get("pain", 0) + emotion_counts.get("unwell", 0)
        if pain_count >= 2:
            alerts.append(f"近3天出现{pain_count}次身体不适信号")
        # 孤独预警
        if emotion_counts.get("lonely", 0) >= 2:
            alerts.append(f"近3天出现{emotion_counts['lonely']}次孤独感信号")

        if alerts:
            return {
                "user_id": user_id,
                "level": "warn" if len(alerts) <= 2 else "urgent",
                "alerts": alerts,
                "emotion_summary": emotion_counts,
                "window_days": 3,
            }

    except Exception as e:
        logger.error(f"检查情绪阈值失败: {e}")
    finally:
        conn.close()

    return None


def notify_couple(alert: Dict, user_name: str = ""):
    """静默通知夫妻"""
    couple_users = _get_couple_users()
    if not couple_users:
        logger.warning("未配置夫妻用户，无法发送情绪预警")
        return

    level_emoji = "⚠️" if alert["level"] == "warn" else "🚨"
    message = f"{level_emoji} 情绪预警\n\n用户：{user_name or alert['user_id']}\n"
    message += "\n".join(f"  • {a}" for a in alert["alerts"])
    message += "\n\n建议适当关心。"

    for user_id in couple_users:
        result = push_text(user_id, message)
        if result.get("errcode") != 0:
            logger.warning(f"预警推送 {user_id} 失败: {result}")
        else:
            logger.info(f"情绪预警已推送给 {user_id}")


def process_message(user_id: str, text: str, user_name: str = "") -> Optional[Dict]:
    """
    主入口：处理一条用户消息，检测情绪并触发预警
    返回预警信息（如果有），否则返回 None
    """
    emotions = detect_emotion(text)
    if emotions:
        log_emotion(user_id, text, emotions)
        logger.info(f"情绪检测: user={user_id}, emotions={[e[1] for e in emotions]}")

    alert = check_alert_threshold(user_id)
    if alert:
        notify_couple(alert, user_name)
        return alert

    return None


def get_emotion_report(user_id: str, days: int = 7) -> str:
    """生成用户近期情绪报告（用于夫妻 debriefing）"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT emotions, created_at FROM emotion_logs
                WHERE user_id=%s AND created_at > NOW() - INTERVAL '%s days'
                ORDER BY created_at DESC
                """,
                (user_id, days)
            )
            rows = c.fetchall()

        if not rows:
            return "近7天情绪平稳，未检测到显著信号。"

        emotion_counts = {}
        for emotions_str, created_at in rows:
            for part in emotions_str.split(", "):
                if ":" in part:
                    label = part.split(":")[1].split("(")[0]
                    emotion_counts[label] = emotion_counts.get(label, 0) + 1

        lines = [f"近{days}天情绪检测摘要："]
        for label, count in sorted(emotion_counts.items(), key=lambda x: -x[1]):
            cn_name = {
                "low_mood": "情绪低落", "irritated": "烦躁",
                "tired": "疲惫", "insomnia": "睡眠问题",
                "pain": "疼痛", "unwell": "身体不适",
                "anxious": "焦虑", "lonely": "孤独感",
                "aging_anxiety": "衰老焦虑", "low_self_worth": "自我价值感低",
                "happy": "开心", "content": "满足", "energetic": "精神",
            }.get(label, label)
            lines.append(f"  • {cn_name}：{count}次")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"生成情绪报告失败: {e}")
        return "情绪报告生成失败。"
    finally:
        conn.close()
