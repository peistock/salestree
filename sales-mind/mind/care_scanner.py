"""
Sales Risk Scanner（销售风险扫描器）
隐性基线扫描：任何域的对话都扫描销售风险/机会信号
由 emotion_sensor.py 升级而来，扩展为销售信号扫描
"""
import logging
from typing import Dict, List, Optional

from mind.memory import get_conn
from mind.wechat import push_text

logger = logging.getLogger(__name__)


# ========== 扫描规则库 ==========

SALES_SIGNAL_RULES = {
    # 竞品信号
    "competitor_mention": {
        "keywords": ["竞品", "竞争对手", "别家", "另一家", "对比", "比你们", "别的供应商", "替换"],
        "severity_keywords": ["已经用", "签了", "定了", "换到", "切到"],
        "action": "note_only",
        "severity_action": "escalate",
    },
    # 价格压力
    "pricing_pressure": {
        "keywords": ["贵", "便宜", "降价", "折扣", "预算", "报价高", "太贵", "有没有优惠", "再低点"],
        "severity_keywords": ["超预算", "批不了", "太贵了", "接受不了"],
        "action": "note_only",
        "severity_action": "escalate",
    },
    # 时间紧迫
    "urgency": {
        "keywords": ["急", "尽快", "今天", "明天", "截止", "马上", "立刻", "赶"],
        "action": "note_only",
    },
    # 负面反馈
    "negative_feedback": {
        "keywords": ["不满意", "失望", "有问题", "投诉", "不好", "不靠谱", "不行", "做不到"],
        "severity_keywords": ["非常不满", "很失望", "要投诉", "终止合作", "不做了"],
        "action": "escalate",
        "severity_action": "escalate",
    },
    # 流失风险
    "churn_risk": {
        "keywords": ["不合作", "终止", "暂停", "停掉", "换供应商", "不续签", "不签了"],
        "action": "escalate",
    },
    # 积极信号
    "positive_signal": {
        "keywords": ["满意", "合作愉快", "确定", "签", "可以走合同", "推进", "有意向", "不错"],
        "action": "note_only",
    },
}


# ========== 核心扫描函数 ==========

def scan(text: str) -> List[Dict]:
    """
    扫描文本，返回 sales_signals 列表
    每个信号: {type, keyword, action, context, severity}
    """
    signals = []
    for rule_name, rule in SALES_SIGNAL_RULES.items():
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
    扫描工具执行结果中的 sales_signals
    用于 search_web / browse_open 等工具返回的内容中也可能包含信号
    """
    return scan(result_text)


# ========== 管理人员通知 ==========

def _get_manager_users() -> List[str]:
    """获取管理人员的 user_id（role 包含 manager / admin，或 entity_type 为 sales 的全部用户）"""
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


def notify_managers_from_signals(user_id: str, user_name: str, signals: List[Dict]):
    """
    根据 sales_signals 决定通知策略
    - escalate: 立即通知管理人员
    - note_only: 记录，不通知（后续 debriefing 汇总）
    """
    escalates = [s for s in signals if s["action"] == "escalate"]

    if not escalates:
        # note_only 的信号只记录，不实时通知
        for s in signals:
            logger.info(f"Sales signal [note_only]: user={user_id}, type={s['type']}, kw={s['keyword']}")
        return

    managers = _get_manager_users()
    if not managers:
        logger.warning("未配置管理人员，无法发送销售预警")
        return

    lines = [f"🚨 销售风险预警"]
    lines.append(f"用户：{user_name or user_id}")
    for s in escalates:
        lines.append(f"  • {s['type']}：检测到「{s['keyword']}」")
    message = "\n".join(lines)

    for target_id in managers:
        try:
            result = push_text(target_id, message)
            if result.get("errcode") != 0:
                logger.warning(f"销售预警推送 {target_id} 失败: {result}")
            else:
                logger.info(f"销售预警已推送给 {target_id}")
        except Exception as e:
            logger.error(f"推送失败: {e}")


# ========== 兼容旧接口（emotion_sensor）==========

def process_message(user_id: str, text: str, user_name: str = "") -> Optional[Dict]:
    """
    兼容 emotion_sensor.process_message 接口
    扫描销售风险/机会信号
    """
    signals = scan(text)
    if signals:
        logger.info(f"Sales scan: user={user_id}, signals={[s['type'] for s in signals]}")
        notify_managers_from_signals(user_id, user_name, signals)
        # 如果有 escalate 信号，返回预警信息
        escalates = [s for s in signals if s["action"] == "escalate"]
        if escalates:
            return {
                "user_id": user_id,
                "level": "urgent",
                "signals": signals,
            }
    return None


# ========== 信号日志（保留原有功能）==========

def log_signals(user_id: str, text: str, signals: List[Dict]):
    """记录 sales 信号日志到数据库"""
    if not signals:
        return

    signal_str = ", ".join(f"{s['type']}:{s['keyword']}({s['action']})" for s in signals)
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO emotion_logs (user_id, source_text, emotions, created_at)
                VALUES (%s, %s, %s, NOW())
                """,
                (user_id, text[:500], signal_str)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"记录 sales 信号日志失败: {e}")
    finally:
        conn.close()


def get_signal_report(user_id: str, days: int = 7) -> str:
    """生成用户近期销售信号报告（用于 debriefing）"""
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
            return f"近{days}天状态平稳，未检测到显著销售信号。"

        type_counts = {}
        for emotions_str, created_at in rows:
            for part in emotions_str.split(", "):
                if ":" in part:
                    label = part.split(":")[0]
                    type_counts[label] = type_counts.get(label, 0) + 1

        cn_map = {
            "competitor_mention": "竞品提及",
            "pricing_pressure": "价格压力",
            "urgency": "时间紧迫",
            "negative_feedback": "负面反馈",
            "churn_risk": "流失风险",
            "positive_signal": "积极信号",
        }

        lines = [f"近{days}天销售信号扫描摘要："]
        for label, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  • {cn_map.get(label, label)}：{count}次")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"生成报告失败: {e}")
        return "销售信号报告生成失败。"
    finally:
        conn.close()
