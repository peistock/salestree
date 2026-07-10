"""
定时任务调度器
- 早 8:00 推送当日事项
- 用药提醒
- 每日凌晨 3:00 记忆摘要
"""
import os
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from mind.companion_routines import morning_routine, noon_routine, evening_routine, morning_news_push, evening_news_push

logger = logging.getLogger(__name__)

DB_URL = f"postgresql://{os.getenv('DB_USER', 'family')}:{os.getenv('DB_PASSWORD', 'familymind2026')}@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'familymind')}"

scheduler = BackgroundScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=DB_URL)}
)


def morning_routine():
    """早 8:00：扫描今日事项，推送给所有人"""
    from mind.wechat import push_text
    from mind.memory import get_conn

    # 读取销售日历
    cal_path = os.path.join(os.getenv("DATA_DIR", "./data"), "knowledge", "family_calendar.txt")
    today = datetime.now().strftime("%m-%d")
    text = "早上好！今日无特殊安排。"

    if os.path.exists(cal_path):
        try:
            with open(cal_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip() and today in l]
            if lines:
                text = "早上好！今日安排：\n" + "\n".join(lines)
        except Exception as e:
            logger.error(f"读取日历失败: {e}")

    # 读取用药提醒
    med_path = os.path.join(os.getenv("DATA_DIR", "./data"), "knowledge", "medication.txt")
    if os.path.exists(med_path):
        try:
            with open(med_path, "r", encoding="utf-8") as f:
                meds = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            if meds:
                text += "\n\n💊 今日用药提醒：\n" + "\n".join(f"  • {m}" for m in meds)
        except Exception as e:
            logger.error(f"读取用药清单失败: {e}")

    # 推送给所有销售人员
    try:
        conn = get_conn()
        with conn.cursor() as c:
            c.execute("SELECT user_id FROM user_profiles WHERE role='长辈'")
            users = c.fetchall()
        conn.close()

        for (user_id,) in users:
            result = push_text(user_id, text)
            if result.get("errcode") != 0:
                logger.warning(f"推送 {user_id} 失败: {result}")

        # 记录任务执行
        conn = get_conn()
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO scheduled_tasks (task_name, status, detail) VALUES (%s, %s, %s)",
                ("morning_routine", "success", f"推送给 {len(users)} 位长辈")
            )
            conn.commit()
        conn.close()

    except Exception as e:
        logger.error(f"早播报失败: {e}")


def daily_consolidation():
    """每日凌晨 3:00：对话摘要与记忆沉淀"""
    logger.info("执行每日记忆整理...")
    # Phase 2 实现：读取昨日对话，生成摘要，存入中期记忆
    pass


def daily_debriefing():
    """每日 21:00：生成晚间销售日报简报（Phase 4）"""
    logger.info("执行每日晚间简报生成...")
    from mind.memory import get_elderly_users, get_conn, save_couple_notification
    from mind.llm_client import chat

    elders = get_elderly_users()
    if not elders:
        logger.warning("晚间简报：未找到销售用户")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    for elder in elders:
        user_id = elder["user_id"]
        user_name = elder.get("name", user_id)
        try:
            conn = get_conn()
            with conn.cursor() as c:
                # 今天对话数
                c.execute(
                    """
                    SELECT COUNT(*) FROM episodic_memory
                    WHERE user_id=%s AND role='user' AND created_at::date=CURRENT_DATE
                    """,
                    (user_id,)
                )
                msg_count = c.fetchone()[0]

                # 今天最后几条对话
                c.execute(
                    """
                    SELECT role, content FROM episodic_memory
                    WHERE user_id=%s AND created_at::date=CURRENT_DATE
                    ORDER BY created_at DESC LIMIT 10
                    """,
                    (user_id,)
                )
                msgs = c.fetchall()
            conn.close()

            if msg_count == 0:
                continue

            dialogue = "\n".join([
                f"{'用户' if r[0] == 'user' else '销销'}：{r[1][:150]}"
                for r in reversed(msgs)
            ])

            narrative = chat(
                system="你是销售智能助手销销。请根据今天与长辈的对话记录，用温暖亲切的小辈口吻，为夫妻生成一段'今日销售日报'。不超过200字，像讲故事一样自然，包含：今天互动了什么、老人状态如何、有没有需要关注的事。",
                user_prompt=f"今天是{today_str}。与{user_name}的对话记录：\n{dialogue}\n\n请生成今日销售日报：",
                model=MODEL_DAILY,
                max_tokens=400,
                temperature=0.7,
            )

            save_couple_notification(
                user_id=user_id,
                type_="debriefing",
                title=f"{user_name} 的今日叙事",
                content=narrative
            )
            logger.info(f"晚间简报已生成: {user_name}")

        except Exception as e:
            logger.error(f"生成 {user_name} 晚间简报失败: {e}")


def _push_reminder(user_id: str, content: str):
    """到点推送提醒"""
    from mind.wechat import push_text
    from mind.memory import get_conn

    # 查找企微 user_id
    wechat_uid = user_id
    try:
        conn = get_conn()
        with conn.cursor() as c:
            c.execute("SELECT wechat_user_id FROM user_profiles WHERE user_id=%s", (user_id,))
            row = c.fetchone()
            if row and row[0]:
                wechat_uid = row[0]
        conn.close()
    except Exception as e:
        logger.warning(f"查找 wechat_user_id 失败: {e}")

    result = push_text(wechat_uid, f"⏰ 提醒到啦！{content}")
    if result.get("errcode") != 0:
        logger.warning(f"提醒推送失败: {result}")


def add_reminder(user_id: str, content: str, remind_at: datetime):
    """添加一次性提醒"""
    job_id = f"reminder_{user_id}_{int(remind_at.timestamp())}"
    scheduler.add_job(
        _push_reminder,
        trigger='date',
        run_date=remind_at,
        args=[user_id, content],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=3600  # 1小时内即使延迟也执行
    )
    logger.info(f"已添加提醒: {user_id} at {remind_at}, content={content}")
    return job_id


def init_scheduler():
    """初始化定时任务"""
    # Phase 2.x：早安仪式（7:30）
    scheduler.add_job(
        morning_routine,
        CronTrigger(hour=7, minute=30),
        id="morning_routine",
        replace_existing=True
    )
    # Phase 2.x：午间关怀（11:30）
    scheduler.add_job(
        noon_routine,
        CronTrigger(hour=11, minute=30),
        id="noon_routine",
        replace_existing=True
    )
    # Phase 2.x：晚间放松（20:00）
    scheduler.add_job(
        evening_routine,
        CronTrigger(hour=20, minute=0),
        id="evening_routine",
        replace_existing=True
    )
    scheduler.add_job(
        daily_consolidation,
        CronTrigger(hour=3, minute=0),
        id="daily_consolidation",
        replace_existing=True
    )
    # Phase 4：晚间销售日报（21:00）
    scheduler.add_job(
        daily_debriefing,
        CronTrigger(hour=21, minute=0),
        id="daily_debriefing",
        replace_existing=True
    )
    # 每日早报推送（8:00）
    scheduler.add_job(
        morning_news_push,
        CronTrigger(hour=8, minute=0),
        id="morning_news_push",
        replace_existing=True
    )
    # 每日晚报推送（18:00）
    scheduler.add_job(
        evening_news_push,
        CronTrigger(hour=18, minute=0),
        id="evening_news_push",
        replace_existing=True
    )

    scheduler.start()
    logger.info("定时任务已启动")
