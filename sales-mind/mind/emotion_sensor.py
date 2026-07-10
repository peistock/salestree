"""
沟通情绪感知模块
- 文字层情绪检测：关键词 + 对话行为模式
- 情绪日志记录
- 触发阈值后通知管理人员
"""
import logging
from typing import Dict, List, Optional, Tuple

from mind.memory import get_conn
from mind.wechat import push_text

logger = logging.getLogger(__name__)

# ========== 沟通情绪词典 ==========
# 关键词 → 情绪标签 + 权重
COMMOTION_KEYWORDS = {
    # 负面沟通信号（权重高）
    "不满意": ("dissatisfied", 3),
    "失望": ("dissatisfied", 3),
    "太慢": ("frustrated", 2),
    "太慢了": ("frustrated", 3),
    "不靠谱": ("frustrated", 3),
    "不行": ("frustrated", 2),
    "有问题": ("concerned", 2),
    "担心": ("concerned", 2),
    "犹豫": ("hesitant", 2),
    "考虑一下": ("hesitant", 2),
    "再想想": ("hesitant", 2),
    "贵": ("price_sensitive", 2),
    "太贵": ("price_sensitive", 3),
    "预算不够": ("price_sensitive", 3),

    # 积极沟通信号（用于平衡）
    "满意": ("satisfied", 2),
    "不错": ("satisfied", 2),
    "很好": ("satisfied", 2),
    "确定": ("committed", 3),
    "签合同": ("committed", 3),
    "推进": ("interested", 2),
    "有意向": ("interested", 2),
    "期待": ("interested", 2),
}

# 行为模式阈值
BEHAVIOR_THRESHOLDS = {
    "dissatisfied_3day": 2,    # 3 天内负面反馈关键词出现 2 次以上
    "frustrated_3day": 2,      # 3 天内挫败关键词出现 2 次以上
    "hesitant_3day": 2,        # 3 天内犹豫关键词出现 2 次以上
}


def _get_manager_users() -> List[str]:
    """获取管理人员的 user_id"""
    conn = get_conn()
    users = []
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id FROM user_profiles WHERE role ILIKE '%manager%' OR role ILIKE '%admin%' OR entity_type='sales'")
            users = [r[0] for r in c.fetchall()]
    except Exception as e:
        logger.error(f"获取管理人员失败: {e}")
    finally:
        conn.close()
    return users


def detect_emotion(text: str) -> List[Tuple[str, str, int]]:
    """
    从文本中检测沟通情绪信号
    返回: [(关键词, 情绪标签, 权重), ...]
    """
    found = []
    sorted_keywords = sorted(COMMOTION_KEYWORDS.keys(), key=len, reverse=True)
    remaining = text
    for kw in sorted_keywords:
        if kw in remaining:
            emotion, weight = COMMOTION_KEYWORDS[kw]
            found.append((kw, emotion, weight))
            remaining = remaining.replace(kw, "", 1)
    return found


def log_emotion(user_id: str, text: str, emotions: List[Tuple[str, str, int]]):
    """记录沟通情绪日志到数据库"""
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
        logger.error(f"记录沟通情绪日志失败: {e}")
    finally:
        conn.close()


def check_alert_threshold(user_id: str) -> Optional[Dict]:
    """
    检查是否触发沟通情绪预警阈值
    返回预警信息字典，或 None
    """
    conn = get_conn()
    try:
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

        emotion_counts = {}
        for (emotions_str,) in rows:
            for part in emotions_str.split(", "):
                if ":" in part:
                    label = part.split(":")[1].split("(")[0]
                    emotion_counts[label] = emotion_counts.get(label, 0) + 1

        alerts = []
        if emotion_counts.get("dissatisfied", 0) >= BEHAVIOR_THRESHOLDS["dissatisfied_3day"]:
            alerts.append(f"近3天出现{emotion_counts['dissatisfied']}次客户不满信号")
        if emotion_counts.get("frustrated", 0) >= BEHAVIOR_THRESHOLDS["frustrated_3day"]:
            alerts.append(f"近3天出现{emotion_counts['frustrated']}次客户挫败信号")
        if emotion_counts.get("hesitant", 0) >= BEHAVIOR_THRESHOLDS["hesitant_3day"]:
            alerts.append(f"近3天出现{emotion_counts['hesitant']}次客户犹豫信号")

        if alerts:
            return {
                "user_id": user_id,
                "level": "warn" if len(alerts) <= 2 else "urgent",
                "alerts": alerts,
                "emotion_summary": emotion_counts,
                "window_days": 3,
            }

    except Exception as e:
        logger.error(f"检查沟通情绪阈值失败: {e}")
    finally:
        conn.close()

    return None


def notify_managers(alert: Dict, user_name: str = ""):
    """通知管理人员"""
    managers = _get_manager_users()
    if not managers:
        logger.warning("未配置管理人员，无法发送沟通情绪预警")
        return

    level_emoji = "⚠️" if alert["level"] == "warn" else "🚨"
    message = f"{level_emoji} 沟通情绪预警\n\n用户：{user_name or alert['user_id']}\n"
    message += "\n".join(f"  • {a}" for a in alert["alerts"])
    message += "\n\n建议适当关注。"

    for user_id in managers:
        result = push_text(user_id, message)
        if result.get("errcode") != 0:
            logger.warning(f"沟通情绪预警推送 {user_id} 失败: {result}")
        else:
            logger.info(f"沟通情绪预警已推送给 {user_id}")


def process_message(user_id: str, text: str, user_name: str = "") -> Optional[Dict]:
    """
    主入口：处理一条用户消息，检测沟通情绪并触发预警
    返回预警信息（如果有），否则返回 None
    """
    emotions = detect_emotion(text)
    if emotions:
        log_emotion(user_id, text, emotions)
        logger.info(f"沟通情绪检测: user={user_id}, emotions={[e[1] for e in emotions]}")

    alert = check_alert_threshold(user_id)
    if alert:
        notify_managers(alert, user_name)
        return alert

    return None


def get_emotion_report(user_id: str, days: int = 7) -> str:
    """生成用户近期沟通情绪报告"""
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
            return "近7天沟通情绪平稳，未检测到显著信号。"

        emotion_counts = {}
        for emotions_str, created_at in rows:
            for part in emotions_str.split(", "):
                if ":" in part:
                    label = part.split(":")[1].split("(")[0]
                    emotion_counts[label] = emotion_counts.get(label, 0) + 1

        lines = [f"近{days}天沟通情绪检测摘要："]
        for label, count in sorted(emotion_counts.items(), key=lambda x: -x[1]):
            cn_name = {
                "dissatisfied": "不满", "frustrated": "挫败",
                "concerned": "担忧", "hesitant": "犹豫",
                "price_sensitive": "价格敏感",
                "satisfied": "满意", "committed": " committed", "interested": "感兴趣",
            }.get(label, label)
            lines.append(f"  • {cn_name}：{count}次")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"生成沟通情绪报告失败: {e}")
        return "沟通情绪报告生成失败。"
    finally:
        conn.close()
