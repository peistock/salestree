"""
陪伴面仪式模块（Phase 2.x）
早安 / 午间 / 晚间 三个固定触点
- 组装记忆上下文 → LLM 生成话术 → 推送到企微
- 支持老人"今天别播报"的临时跳过指令
"""
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from mind.llm_client import chat
from mind.memory import get_conn
from mind.wechat import push_text

logger = logging.getLogger(__name__)

MODEL_DAILY = os.getenv("MODEL_DAILY", "deepseek-chat")

# 仪式配置
ROUTINES = {
    "morning": {
        "name": "早安仪式",
        "hour": 7,
        "minute": 30,
        "system_prompt": """你是销售智能助手销销，正在执行早安仪式。
规则：
1. 语气亲切温暖，像家人一样
2. 每段不超过30字，老人看得不累
3. 先报天气，再报今日用药/日程，最后一句轻松开场白
4. 涉及用药必须追加「建议咨询医生确认」
5. 用"今天"开头，不要说"早安仪式"这几个字
6. 总长度控制在200字以内，合并成一条消息""",
        "context_keys": ["weather", "medication", "today_schedule", "last_night_sleep"],
    },
    "noon": {
        "name": "午间关怀",
        "hour": 11,
        "minute": 30,
        "system_prompt": """你是销售智能助手销销，正在执行午间关怀。
规则：
1. 提醒吃饭，语气像家人
2. 带一句轻松的闲聊（昨晚睡得怎么样/今天天气不错可以出去走走）
3. 每段不超过30字
4. 总长度控制在150字以内""",
        "context_keys": ["weather", "mood_check"],
    },
    "evening": {
        "name": "晚间放松",
        "hour": 20,
        "minute": 0,
        "system_prompt": """你是销售智能助手销销，正在执行晚间放松。
规则：
1. 简短轻松的内容（可以是今天的一件趣事、一句暖心的话、或者明天天气预告）
2. 不要讲长故事，控制在100字以内
3. 结尾温和地问一句"今天过得怎么样？"或者"早点休息"
4. 每段不超过30字""",
        "context_keys": ["tomorrow_weather", "today_summary"],
    },
}


def _should_skip(user_id: str, routine_name: str) -> bool:
    """检查老人是否说过「今天别播报」"""
    try:
        conn = get_conn()
        with conn.cursor() as c:
            # 最近 24 小时内是否有跳过指令
            c.execute(
                """
                SELECT content FROM episodic_memory
                WHERE user_id=%s AND role='user'
                  AND content LIKE '%别播报%'
                  AND created_at > NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id,)
            )
            row = c.fetchone()
            if row:
                # 检查是否同时撤销了
                c.execute(
                    """
                    SELECT content FROM episodic_memory
                    WHERE user_id=%s AND role='user'
                      AND (content LIKE '%恢复播报%' OR content LIKE '%继续播报%')
                      AND created_at > (SELECT created_at FROM episodic_memory WHERE user_id=%s AND role='user' AND content LIKE '%别播报%' ORDER BY created_at DESC LIMIT 1)
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (user_id, user_id)
                )
                revoke = c.fetchone()
                if revoke:
                    return False  # 已撤销跳过
                return True  # 应跳过
        conn.close()
    except Exception as e:
        logger.warning(f"检查跳过状态失败: {e}")
    return False


def _gather_context(user_id: str, keys: List[str]) -> str:
    """根据仪式需要的上下文 key，组装记忆文本"""
    parts = []
    conn = get_conn()
    try:
        for key in keys:
            if key == "weather":
                parts.append("天气：待查询（世界记忆模块接入后自动填充）")
            elif key == "medication":
                c = conn.cursor()
                c.execute(
                    """
                    SELECT content FROM episodic_memory
                    WHERE user_id=%s AND (content LIKE '%药%' OR tags LIKE '%用药%')
                    ORDER BY created_at DESC LIMIT 3
                    """,
                    (user_id,)
                )
                meds = [r[0][:80] for r in c.fetchall()]
                if meds:
                    parts.append("近期用药记录：" + "；".join(meds))
                else:
                    parts.append("用药记录：暂无")
                c.close()
            elif key == "today_schedule":
                c = conn.cursor()
                c.execute(
                    """
                    SELECT summary_text FROM memory_summaries
                    WHERE user_id=%s AND summary_text LIKE '%今天%'
                    ORDER BY created_at DESC LIMIT 2
                    """,
                    (user_id,)
                )
                schedules = [r[0][:100] for r in c.fetchall()]
                if schedules:
                    parts.append("今日日程：" + "；".join(schedules))
                c.close()
            elif key == "last_night_sleep":
                c = conn.cursor()
                c.execute(
                    """
                    SELECT content FROM episodic_memory
                    WHERE user_id=%s AND (content LIKE '%睡%' OR tags LIKE '%睡眠%')
                    ORDER BY created_at DESC LIMIT 2
                    """,
                    (user_id,)
                )
                sleeps = [r[0][:80] for r in c.fetchall()]
                if sleeps:
                    parts.append("近期睡眠：" + "；".join(sleeps))
                c.close()
            elif key == "mood_check":
                c = conn.cursor()
                c.execute(
                    """
                    SELECT content FROM episodic_memory
                    WHERE user_id=%s AND role='user'
                    ORDER BY created_at DESC LIMIT 5
                    """,
                    (user_id,)
                )
                recent = [r[0][:60] for r in c.fetchall()]
                if recent:
                    parts.append("近期对话：" + "；".join(recent))
                c.close()
    except Exception as e:
        logger.error(f"组装仪式上下文失败: {e}")
    finally:
        conn.close()

    return "\n".join(parts) if parts else "（无额外上下文）"


def _get_elderly_users() -> List[Dict]:
    """获取所有长辈用户"""
    conn = get_conn()
    users = []
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id, name, wechat_user_id FROM user_profiles WHERE role='长辈'")
            for row in c.fetchall():
                users.append({"user_id": row[0], "name": row[1], "wechat_user_id": row[2] or ""})
    except Exception as e:
        logger.error(f"获取长辈列表失败: {e}")
    finally:
        conn.close()
    return users


def _generate_routine_message(routine_type: str, user_name: str, context: str) -> str:
    """调用 LLM 生成仪式消息"""
    cfg = ROUTINES[routine_type]
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M %A")

    try:
        return chat(
            system=cfg["system_prompt"],
            user_prompt=f"当前时间：{now}\n用户：{user_name}\n\n相关记忆：\n{context}\n\n请生成今天的{cfg['name']}消息：",
            model=MODEL_DAILY,
            max_tokens=600,
            temperature=0.7,
        )
    except Exception as e:
        logger.error(f"生成{cfg['name']}失败: {e}")
        # 降级：固定模板
        if routine_type == "morning":
            return f"{user_name}，早上好！今天记得按时吃药，有事随时找我。"
        elif routine_type == "noon":
            return f"{user_name}，该吃午饭啦，多吃点蔬菜。"
        else:
            return f"{user_name}，晚上好，今天辛苦了，早点休息。"


def run_routine(routine_type: str):
    """执行指定仪式，推送给所有长辈"""
    cfg = ROUTINES.get(routine_type)
    if not cfg:
        logger.error(f"未知仪式类型: {routine_type}")
        return

    users = _get_elderly_users()
    if not users:
        logger.warning(f"{cfg['name']}: 未找到长辈用户")
        return

    logger.info(f"开始执行 {cfg['name']}，推送 {len(users)} 位长辈")

    for user in users:
        user_id = user["user_id"]
        user_name = user["name"] or ""
        wechat_uid = user.get("wechat_user_id", "") or user_id

        if not wechat_uid:
            logger.warning(f"{user_name} 未配置企微 user_id，跳过推送")
            continue

        if _should_skip(user_id, routine_type):
            logger.info(f"{user_name} 设置了跳过 {cfg['name']}")
            continue

        context = _gather_context(user_id, cfg["context_keys"])
        message = _generate_routine_message(routine_type, user_name, context)

        result = push_text(wechat_uid, message)
        if result.get("errcode") != 0:
            logger.warning(f"推送给 {user_name} 失败: {result}")
        else:
            logger.info(f"推送给 {user_name} 成功")

    logger.info(f"{cfg['name']} 执行完成")


# 快捷函数
def morning_routine():
    run_routine("morning")


def noon_routine():
    run_routine("noon")


def evening_routine():
    run_routine("evening")


def daily_news_push(period: str = "morning"):
    """每日简报推送：从 tophub 抓取早报/晚报，生成简报后推送给长辈
    period: morning(早报) | evening(晚报)
    """
    from mind.news_briefing import get_daily_briefing

    users = _get_elderly_users()
    if not users:
        logger.warning(f"今日{period}: 未找到长辈用户")
        return

    label = "早报" if period == "morning" else "晚报"
    logger.info(f"开始生成今日{label}，推送 {len(users)} 位长辈")

    briefing = get_daily_briefing(period=period)
    if not briefing or f"今天{label}还没更新" in briefing:
        logger.warning(f"今日{label}生成失败或内容为空")
        return

    for user in users:
        user_id = user["user_id"]
        user_name = user["name"] or ""
        wechat_uid = user.get("wechat_user_id", "") or user_id

        if not wechat_uid:
            logger.warning(f"{user_name} 未配置企微 user_id，跳过推送")
            continue

        result = push_text(wechat_uid, briefing)
        if result.get("errcode") != 0:
            logger.warning(f"{label}推送给 {user_name} 失败: {result}")
        else:
            logger.info(f"{label}推送给 {user_name} 成功")

    logger.info(f"今日{label}推送完成")


def morning_news_push():
    """早报推送快捷函数"""
    daily_news_push("morning")


def evening_news_push():
    """晚报推送快捷函数"""
    daily_news_push("evening")
