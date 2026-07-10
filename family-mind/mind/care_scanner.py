"""
Care Scanner（v1.1）
隐性基线扫描：任何域的对话都扫描健康/安全/情绪信号
由 emotion_sensor.py 升级而来，扩展为三维扫描
"""
import os
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from mind.memory import get_conn
from mind.wechat import push_text

logger = logging.getLogger(__name__)


# ========== 扫描规则库 ==========

CARE_RULES = {
    # 健康信号
    "health_mention": {
        "keywords": ["疼", "痛", "晕", "咳", "血压", "血糖", "药", "医院", "不舒服", "难受", "胸闷", "气短"],
        "severity_keywords": ["疼得厉害", "喘不过气", "晕倒", "出血", "急救", "120", "不行了"],
        "action": "note_only",
        "severity_action": "escalate",
    },
    # 安全信号
    "scam_link": {
        "keywords": ["链接", "点击", "中奖", "免费", "转账", "验证码", "领红包", "恭喜您"],
        "action": "intercept",
    },
    "scam_phone": {
        "keywords": ["公安局", "法院", "检察院", "涉嫌", "冻结", "安全账户", "配合调查"],
        "action": "intercept",
    },
    # 情绪信号
    "mood_low": {
        "keywords": ["没意思", "睡不着", "不想活", "活够了", "遗嘱", "交代后事", "走了算了"],
        "action": "escalate",
    },
    "mood_positive": {
        "keywords": ["开心", "高兴", "好玩", "精神", "不错", "蛮好", "舒服"],
        "action": "note_only",
    },
    "lonely": {
        "keywords": ["想儿子", "想孙子", "想女儿", "想外孙", "孤单", "没人说话", "冷清"],
        "action": "note_only",
    },
    "aging_anxiety": {
        "keywords": ["老了", "没用", "拖后腿", "累赘", "不中用"],
        "action": "escalate",
    },
}


# ========== 核心扫描函数 ==========

def scan(text: str) -> List[Dict]:
    """
    扫描文本，返回 care_signals 列表
    每个信号: {type, keyword, action, context, severity}
    """
    signals = []
    for rule_name, rule in CARE_RULES.items():
        matched = False
        matched_kw = ""
        severity = False

        # 先检查严重关键词
        if "severity_keywords" in rule:
            for kw in rule["severity_keywords"]:
                if kw in text:
                    matched = True
                    matched_kw = kw
                    severity = True
                    break

        # 再检查普通关键词
        if not matched:
            for kw in rule["keywords"]:
                if kw in text:
                    matched = True
                    matched_kw = kw
                    break

        if matched:
            action = rule.get("severity_action", rule["action"]) if severity else rule["action"]
            signals.append({
                "type": rule_name,
                "keyword": matched_kw,
                "action": action,
                "context": text[:100],
                "severity": severity,
            })

    return signals


def scan_tool_result(tool_name: str, result_text: str) -> List[Dict]:
    """
    扫描工具执行结果中的 care_signals
    用于 search_web / browse_open 等工具返回的内容中也可能包含信号
    """
    return scan(result_text)


# ========== 夫妻通知 ==========

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


def notify_couple_from_signals(user_id: str, user_name: str, signals: List[Dict]):
    """
    根据 care_signals 决定通知策略
    - escalate: 立即通知
    - intercept: 立即通知 + 提醒老人
    - note_only: 记录，不通知（后续 debriefing 汇总）
    """
    escalates = [s for s in signals if s["action"] == "escalate"]
    intercepts = [s for s in signals if s["action"] == "intercept"]

    if not escalates and not intercepts:
        # note_only 的信号只记录，不实时通知
        for s in signals:
            logger.info(f"Care signal [note_only]: user={user_id}, type={s['type']}, kw={s['keyword']}")
        return

    couple_users = _get_couple_users()
    if not couple_users:
        logger.warning("未配置夫妻用户，无法发送 care 预警")
        return

    messages = []
    if escalates:
        lines = [f"🚨 照护预警"]
        lines.append(f"用户：{user_name or user_id}")
        for s in escalates:
            lines.append(f"  • {s['type']}：检测到「{s['keyword']}」")
        messages.append("\n".join(lines))

    if intercepts:
        lines = [f"⚠️ 安全拦截"]
        lines.append(f"用户：{user_name or user_id}")
        for s in intercepts:
            lines.append(f"  • {s['type']}：检测到「{s['keyword']}」")
        messages.append("\n".join(lines))

    for msg in messages:
        for target_id in couple_users:
            try:
                result = push_text(target_id, msg)
                if result.get("errcode") != 0:
                    logger.warning(f"Care 预警推送 {target_id} 失败: {result}")
                else:
                    logger.info(f"Care 预警已推送给 {target_id}")
            except Exception as e:
                logger.error(f"推送失败: {e}")


# ========== 兼容旧接口（emotion_sensor）=========

def process_message(user_id: str, text: str, user_name: str = "") -> Optional[Dict]:
    """
    兼容 emotion_sensor.process_message 接口
    扫描情绪 + 健康 + 安全信号
    """
    signals = scan(text)
    if signals:
        logger.info(f"Care scan: user={user_id}, signals={[s['type'] for s in signals]}")
        notify_couple_from_signals(user_id, user_name, signals)
        # 如果有 escalate 信号，返回预警信息
        escalates = [s for s in signals if s["action"] == "escalate"]
        if escalates:
            return {
                "user_id": user_id,
                "level": "urgent",
                "signals": signals,
            }
    return None


# ========== 情绪日志（保留原有功能）=========

def log_emotion(user_id: str, text: str, signals: List[Dict]):
    """记录 care 信号日志到数据库"""
    if not signals:
        return

    emotion_str = ", ".join(f"{s['type']}:{s['keyword']}({s['action']})" for s in signals)
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
        logger.error(f"记录 care 日志失败: {e}")
    finally:
        conn.close()


def get_emotion_report(user_id: str, days: int = 7) -> str:
    """生成用户近期 care 信号报告（用于夫妻 debriefing）"""
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
            return "近7天状态平稳，未检测到显著信号。"

        type_counts = {}
        for emotions_str, created_at in rows:
            for part in emotions_str.split(", "):
                if ":" in part:
                    label = part.split(":")[0]
                    type_counts[label] = type_counts.get(label, 0) + 1

        cn_map = {
            "health_mention": "健康提及", "scam_link": "可疑链接",
            "scam_phone": "诈骗电话", "mood_low": "情绪低落",
            "mood_positive": "积极情绪", "lonely": "孤独感",
            "aging_anxiety": "衰老焦虑",
        }

        lines = [f"近{days}天照护扫描摘要："]
        for label, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  • {cn_map.get(label, label)}：{count}次")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"生成报告失败: {e}")
        return "照护报告生成失败。"
    finally:
        conn.close()
