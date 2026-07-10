"""
销售仪式模块
早间简报 / 午间资讯 / 晚间复盘 三个固定触点
- 组装销售上下文 → LLM 生成话术 → 推送到企微
- 支持用户"今天别播报"的临时跳过指令
"""
import os
import logging
from datetime import datetime
from typing import List, Dict

from mind.llm_client import chat
from mind.memory import get_conn
from mind.wechat import push_text

logger = logging.getLogger(__name__)

MODEL_DAILY = os.getenv("MODEL_DAILY", "deepseek-chat")

# 仪式配置
ROUTINES = {
    "morning": {
        "name": "早间销售简报",
        "hour": 7,
        "minute": 30,
        "system_prompt": """你是销售智能助手销销，正在执行早间销售简报。
规则：
1. 语气专业、利落，像销售搭档
2. 每段不超过40字
3. 先报天气和日期，再报今日重点客户/商机，最后一句鼓励开场白
4. 用"今天"开头，不要说"早间简报"这几个字
5. 总长度控制在200字以内，合并成一条消息""",
        "context_keys": ["weather", "today_deals", "today_schedule"],
    },
    "noon": {
        "name": "午间行业快讯",
        "hour": 11,
        "minute": 30,
        "system_prompt": """你是销售智能助手销销，正在执行午间行业快讯。
规则：
1. 分享1-2条对销售有用的行业/客户动态
2. 带一句轻松的提醒（注意休息、下午加油）
3. 每段不超过40字
4. 总长度控制在150字以内""",
        "context_keys": ["weather", "recent_activities"],
    },
    "evening": {
        "name": "晚间复盘",
        "hour": 20,
        "minute": 0,
        "system_prompt": '''你是销售智能助手销销，正在执行晚间复盘。
规则：
1. 简短回顾今天值得记录的客户互动或商机进展
2. 提示明日可跟进的 next_step
3. 控制在120字以内
4. 结尾温和地问一句"今天还有什么需要我帮您收尾的吗？"或"早点休息"''',
        "context_keys": ["tomorrow_weather", "today_summary"],
    },
}


def _should_skip(user_id: str, routine_name: str) -> bool:
    """检查用户是否说过「今天别播报」"""
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
    """根据仪式需要的上下文 key，组装销售上下文文本"""
    parts = []
    conn = get_conn()
    try:
        for key in keys:
            if key == "weather":
                parts.append("天气：待查询（世界记忆模块接入后自动填充）")
            elif key == "today_deals":
                c = conn.cursor()
                c.execute(
                    """
                    SELECT name, stage, next_step FROM deals
                    WHERE owner_id=%s
                    ORDER BY updated_at DESC LIMIT 3
                    """,
                    (user_id,)
                )
                deals = [f"{r[0]}（{r[1]}，下一步：{r[2] or '无'}）" for r in c.fetchall()]
                if deals:
                    parts.append("近期商机：" + "；".join(deals))
                else:
                    parts.append("商机：暂无")
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
            elif key == "recent_activities":
                c = conn.cursor()
                c.execute(
                    """
                    SELECT content FROM activities
                    WHERE owner_id=%s
                    ORDER BY created_at DESC LIMIT 3
                    """,
                    (user_id,)
                )
                acts = [r[0][:80] for r in c.fetchall()]
                if acts:
                    parts.append("近期活动：" + "；".join(acts))
                c.close()
            elif key == "today_summary":
                c = conn.cursor()
                c.execute(
                    """
                    SELECT content FROM episodic_memory
                    WHERE user_id=%s AND role='user'
                      AND created_at::date=CURRENT_DATE
                    ORDER BY created_at DESC LIMIT 5
                    """,
                    (user_id,)
                )
                recent = [r[0][:60] for r in c.fetchall()]
                if recent:
                    parts.append("今日对话：" + "；".join(recent))
                c.close()
    except Exception as e:
        logger.error(f"组装仪式上下文失败: {e}")
    finally:
        conn.close()

    return "\n".join(parts) if parts else "（无额外上下文）"


def _get_sales_users() -> List[Dict]:
    """获取所有销售用户"""
    conn = get_conn()
    users = []
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id, name, wechat_user_id FROM user_profiles WHERE entity_type='sales'")
            for row in c.fetchall():
                users.append({"user_id": row[0], "name": row[1], "wechat_user_id": row[2] or ""})
    except Exception as e:
        logger.error(f"获取销售用户列表失败: {e}")
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
            return f"{user_name}，早上好！今天记得跟进重点客户，有事随时找我。"
        elif routine_type == "noon":
            return f"{user_name}，午休愉快，下午继续加油。"
        else:
            return f"{user_name}，晚上好，今天辛苦了，早点休息。"


def run_routine(routine_type: str):
    """执行指定仪式，推送给所有销售用户"""
    cfg = ROUTINES.get(routine_type)
    if not cfg:
        logger.error(f"未知仪式类型: {routine_type}")
        return

    users = _get_sales_users()
    if not users:
        logger.warning(f"{cfg['name']}: 未找到销售用户")
        return

    logger.info(f"开始执行 {cfg['name']}，推送 {len(users)} 位销售用户")

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
    """每日简报推送：从 tophub 抓取早报/晚报，生成简报后推送给销售用户
    period: morning(早报) | evening(晚报)
    """
    from mind.news_briefing import get_daily_briefing

    users = _get_sales_users()
    if not users:
        logger.warning(f"今日{period}: 未找到销售用户")
        return

    label = "早报" if period == "morning" else "晚报"
    logger.info(f"开始生成今日{label}，推送 {len(users)} 位销售用户")

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
