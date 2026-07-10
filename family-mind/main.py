"""
销销 - FastAPI 主服务
- 企微消息接收与回复
- 语音消息处理入口
- 健康检查
- 启动时初始化数据库和定时任务
"""
import os
import random
import time
import logging
import json
import threading
import subprocess
import asyncio
import re
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
load_dotenv(override=True)

# 代理配置：如果设置了代理环境变量，应用到全局 HTTP 请求
# 用于固定出站 IP（如通过云主机 tinyproxy 访问企微 API）
for _proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    _proxy_val = os.getenv(_proxy_key)
    if _proxy_val:
        os.environ[_proxy_key] = _proxy_val
# 本地服务不走代理（SearXNG、CDP Proxy、peistock 等）
os.environ["NO_PROXY"] = "127.0.0.1,localhost,*.local"

# macOS WeasyPrint 需要 brew 库路径
if os.path.exists("/opt/homebrew/lib"):
    os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

from fastapi import FastAPI, Request, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketState

from mind.wechat import parse_message as wecom_parse_message, download_media
from mind.mp_client import parse_message as mp_parse_message, verify_signature as mp_verify_signature, build_reply_xml
from mind.channel import MessageChannel, WeComChannel, MpChannel
from mind.agent import FamilyAgent
from mind.agent_events import AgentEvent, AgentEventType
from mind.agent_runner import process_query, get_user_name
from mind.memory import init_db, get_conn, Memory
from mind.scheduler import init_scheduler
from mind import wechat_digest

# ========== 日志配置 ==========
LOG_DIR = os.path.join(os.getenv("DATA_DIR", "./data"), "..")
LOG_DIR = os.path.abspath(os.path.join(os.getenv("DATA_DIR", "./data"), "../logs"))
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "app.log"), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== 消息去重缓存（内存级，重启清空，生产环境建议用 Redis）==========
_processed_msgs = set()

# ========== 用户级串行锁（同一用户任务排队，避免并行冲突）==========
_user_locks: dict[str, threading.Lock] = {}


def _get_user_lock(user_id: str) -> threading.Lock:
    """获取用户的串行锁，不存在则创建"""
    if user_id not in _user_locks:
        _user_locks[user_id] = threading.Lock()
    return _user_locks[user_id]


def _notify_unfinished_checkpoints():
    """启动时扫描未完成的 checkpoint，主动通知用户任务中断"""
    try:
        checkpoint_dir = Path(os.getenv("DATA_DIR", "./data")) / "checkpoints"
        if not checkpoint_dir.exists():
            return
        ttl = FamilyAgent.CHECKPOINT_TTL
        now = time.time()
        for cp_file in checkpoint_dir.glob("*.json"):
            try:
                data = json.loads(cp_file.read_text(encoding="utf-8"))
                if now - data.get("timestamp", 0) > ttl:
                    continue
                user_id = cp_file.stem
                todos = data.get("todos", [])
                in_progress = [t["content"] for t in todos if t.get("status") == "in_progress"]
                pending = [t["content"] for t in todos if t.get("status") == "pending"]
                if in_progress:
                    hint = f"刚才做到「{in_progress[0][:20]}...」这步"
                elif pending:
                    hint = f"还有 {len(pending)} 个任务没做完"
                else:
                    hint = "还有任务没做完"
                ch = WeComChannel()
                ch.send_text(
                    user_id,
                    f"咦，销销刚才好像断了一下，之前的任务还没做完呢——{hint}。您回复「继续」，我就接着干～"
                )
                logger.info(f"已通知用户恢复任务: user={user_id}")
            except Exception as e:
                logger.warning(f"扫描 checkpoint 通知失败: {e}")
    except Exception as e:
        logger.warning(f"启动 checkpoint 扫描失败: {e}")


def _ensure_peistock():
    """检测 peistock API（端口3457），未启动则自动拉起"""
    import urllib.request
    try:
        req = urllib.request.Request("http://localhost:3457/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "ok":
                logger.info("peistock API 已运行（端口3457）")
                return
    except Exception:
        pass

    peistock_dir = Path("/Users/peter/Library/Mobile Documents/com~apple~CloudDocs/操作系统/peistock")
    if not peistock_dir.exists():
        logger.warning("peistock 目录不存在，跳过自动启动")
        return

    logger.info("peistock API 未运行，正在自动启动...")
    try:
        subprocess.Popen(
            ["npx", "tsx", "scripts/api-server.ts"],
            cwd=str(peistock_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(3)
        # 二次验证
        req = urllib.request.Request("http://localhost:3457/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "ok":
                logger.info("peistock API 自动启动成功")
            else:
                logger.warning("peistock API 启动后健康检查异常")
    except Exception as e:
        logger.warning(f"peistock API 自动启动失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化"""
    logger.info("销销 启动中...")
    try:
        init_db()
        init_scheduler()
        logger.info("数据库和定时任务已初始化")
    except Exception as e:
        logger.error(f"启动初始化失败: {e}")
    # 启动 peistock API（如果没在跑）
    _ensure_peistock()
    # 启动完成后，扫描未完成的 checkpoint 并通知用户
    _notify_unfinished_checkpoints()
    yield
    logger.info("销销 关闭")


app = FastAPI(title="销销", lifespan=lifespan)

# 静态文件挂载：Agent 生成的文件可通过 /data/<path> 访问
DATA_DIR_ABS = os.path.abspath(os.getenv("DATA_DIR", "./data"))
app.mount("/data", StaticFiles(directory=DATA_DIR_ABS), name="data")

# 静态文件挂载：资讯工作台内嵌资源通过 /wechat_kb/assets/<path> 访问
WECHAT_KB_ASSETS_DIR = os.path.abspath("third_party/wechat-digest-skill/output/assets")
if os.path.isdir(WECHAT_KB_ASSETS_DIR):
    app.mount("/wechat_kb/assets", StaticFiles(directory=WECHAT_KB_ASSETS_DIR), name="wechat_kb_assets")

# WebSocket 活跃任务取消信号：user_id -> threading.Event
active_cancel_events: dict[str, threading.Event] = {}


# ========== 企微回调验证 ==========
@app.get("/wechat")
def wechat_auth(echostr: str = "", msg_signature: str = "", timestamp: str = "", nonce: str = ""):
    """企微验证回调 URL（支持加密模式）"""
    logger.info(f"企微验证请求: echostr={echostr[:20]}... sig={msg_signature[:20]}...")
    if msg_signature and echostr:
        try:
            from mind.wechat import verify_and_decrypt_echostr
            plaintext = verify_and_decrypt_echostr(msg_signature, timestamp, nonce, echostr)
            return PlainTextResponse(content=plaintext)
        except Exception as e:
            logger.error(f"验证解密失败: {e}")
    return PlainTextResponse(content=echostr or "ok")


# ========== 企微消息接收 ==========
@app.post("/wechat")
async def wechat_msg(request: Request, background_tasks: BackgroundTasks):
    """收到销售人员消息"""
    try:
        xml_bytes = await request.body()
        msg = wecom_parse_message(xml_bytes)

        user_id = msg.get("from_user", "")
        content = msg.get("content", "").strip()
        msg_type = msg.get("msg_type", "text")
        msg_id = msg.get("msg_id", "")

        # 如果解析不到用户信息，可能是加密消息，尝试解密
        if not user_id:
            msg_signature = request.query_params.get("msg_signature", "")
            timestamp = request.query_params.get("timestamp", "")
            nonce = request.query_params.get("nonce", "")
            if msg_signature:
                try:
                    from mind.wechat import decrypt_msg
                    logger.info(f"尝试解密: sig={msg_signature[:20]}... ts={timestamp} nonce={nonce}")
                    plaintext = decrypt_msg(xml_bytes, msg_signature, timestamp, nonce)
                    msg = wecom_parse_message(plaintext.encode("utf-8"))
                    user_id = msg.get("from_user", "")
                    content = msg.get("content", "").strip()
                    msg_type = msg.get("msg_type", "text")
                    msg_id = msg.get("msg_id", "")
                    logger.info(f"解密成功: from={user_id}, content={content[:30]}")
                except Exception as e:
                    logger.error(f"解密消息失败: {type(e).__name__}: {repr(e)}")

        if not user_id or not msg_id:
            return "<xml><ReturnCode>0</ReturnCode></xml>"

        # 消息去重
        if msg_id in _processed_msgs:
            logger.debug(f"消息去重: {msg_id}")
            return "<xml><ReturnCode>0</ReturnCode></xml>"
        _processed_msgs.add(msg_id)
        # 限制缓存大小
        if len(_processed_msgs) > 10000:
            _processed_msgs.clear()

        # 立即回一个"收到"（被动回复必须在 5 秒内返回）
        reply_xml = f"""<xml>
<ToUserName><![CDATA[{user_id}]]></ToUserName>
<FromUserName><![CDATA[{msg.get('to_user', '')}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[收到，我想一下...]]></Content>
</xml>"""

        # 后台异步处理
        if msg_type == "voice":
            background_tasks.add_task(process_voice, user_id, msg.get("media_id", ""), msg_id)
        elif msg_type == "image":
            background_tasks.add_task(process_image, user_id, msg.get("media_id", ""), msg_id)
        else:
            background_tasks.add_task(process_text, user_id, content)

        return PlainTextResponse(content=reply_xml, media_type="application/xml")

    except Exception as e:
        logger.error(f"处理企微消息异常: {e}", exc_info=True)
        return "<xml><ReturnCode>0</ReturnCode></xml>"


# ========== 微信公众号自动身份识别 ==========
_MP_AUTO_USERS_FILE = Path(os.getenv("DATA_DIR", "./data")) / "mp_auto_users.json"
_mp_auto_users = {}   # openid -> family_id（自动映射/临时身份）
_mp_pending = {}      # openid -> 等待身份确认
_mp_visitor_counter = 0

_MP_IDENTITY_KEYWORDS = {
    "爷爷": "grandpa",
    "奶奶": "grandma",
    "外公": "grandpa2",
    "外婆": "grandma2",
    "爸爸": "dad",
    "妈妈": "mom",
}


def _load_mp_auto_users():
    """启动时加载自动映射的用户"""
    global _mp_auto_users, _mp_visitor_counter
    if _MP_AUTO_USERS_FILE.exists():
        try:
            data = json.loads(_MP_AUTO_USERS_FILE.read_text(encoding="utf-8"))
            _mp_auto_users = data.get("mapping", {})
            _mp_visitor_counter = data.get("visitor_counter", 0)
            logger.info(f"已加载公众号自动映射用户 {len(_mp_auto_users)} 个")
        except Exception as e:
            logger.warning(f"加载 mp_auto_users 失败: {e}")


def _save_mp_auto_users():
    """保存自动映射到文件"""
    try:
        data = {
            "mapping": _mp_auto_users,
            "visitor_counter": _mp_visitor_counter,
            "updated_at": int(time.time()),
        }
        _MP_AUTO_USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"保存 mp_auto_users 失败: {e}")


def _resolve_mp_user(openid: str, content: str) -> tuple:
    """
    解析公众号用户身份。
    返回 (family_id, is_new, reply_hint)
    - family_id: 映射后的销售身份 ID（或 visitor_xxx）
    - is_new: 是否新映射
    - reply_hint: 如需回复引导语，返回字符串；否则返回空
    """
    global _mp_visitor_counter

    # 1. 已配置在 .env 中的用户
    env_mapping = json.loads(os.getenv("MP_USERS", "{}"))
    if openid in env_mapping:
        return env_mapping[openid], False, ""

    # 2. 已自动映射的用户
    if openid in _mp_auto_users:
        return _mp_auto_users[openid], False, ""

    # 3. 正在等待确认身份的用户
    if openid in _mp_pending:
        # 尝试从消息中提取身份
        matched_identity = None
        for keyword, fid in _MP_IDENTITY_KEYWORDS.items():
            if keyword in content:
                matched_identity = fid
                break

        if matched_identity:
            del _mp_pending[openid]
            # 检查该身份是否已被占用（env + auto 合并检查）
            all_mapping = {**env_mapping, **_mp_auto_users}
            occupied = [v for v in all_mapping.values() if v == matched_identity]
            if not occupied:
                # 第一次说，直接对应
                _mp_auto_users[openid] = matched_identity
                _save_mp_auto_users()
                logger.info(f"公众号自动映射: {openid} -> {matched_identity}")
                return matched_identity, True, f"你好{keyword}～销销记住你啦！"
            else:
                # 第二次说（身份已被占用），分配临时身份
                _mp_visitor_counter += 1
                visitor_id = f"visitor_{_mp_visitor_counter}"
                _mp_auto_users[openid] = visitor_id
                _save_mp_auto_users()
                logger.info(f"公众号临时身份: {openid} -> {visitor_id} (试图认领 {matched_identity})")
                return visitor_id, True, f"你好呀～这个身份已经有主啦，先给你个临时身份，我叫你「访客{_mp_visitor_counter}」吧～"
        else:
            # 没有识别到身份关键词，继续问
            return None, False, "你好呀～请问你是哪位销售同事？请回复你的名字或工号。"

    # 4. 全新用户，首次发消息
    _mp_pending[openid] = {"timestamp": time.time()}
    return None, False, "你好呀～销销收到你的消息啦！请问你是哪位销售同事？请回复你的名字或工号。"


# 启动时加载自动映射
_load_mp_auto_users()


@app.get("/wechat/mp")
def mp_auth(signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""):
    """公众号验证回调 URL"""
    logger.info(f"公众号验证请求: sig={signature[:20]}... ts={timestamp} nonce={nonce}")
    if mp_verify_signature(signature, timestamp, nonce):
        return PlainTextResponse(content=echostr or "ok")
    return PlainTextResponse(content="fail")


@app.post("/wechat/mp")
async def mp_msg(request: Request, background_tasks: BackgroundTasks):
    """收到公众号粉丝消息"""
    try:
        xml_bytes = await request.body()
        msg = mp_parse_message(xml_bytes)

        openid = msg.get("from_user", "")
        content = msg.get("content", "").strip()
        msg_type = msg.get("msg_type", "text")
        msg_id = msg.get("msg_id", "")

        if not openid or not msg_id:
            return PlainTextResponse(content="success")

        # 消息去重
        dedup_key = f"mp_{msg_id}"
        if dedup_key in _processed_msgs:
            return PlainTextResponse(content="success")
        _processed_msgs.add(dedup_key)
        if len(_processed_msgs) > 10000:
            _processed_msgs.clear()

        # 解析用户身份
        family_id, is_new, hint = _resolve_mp_user(openid, content)

        if not family_id:
            # 还没确认身份，返回引导语
            reply_xml = build_reply_xml(openid, msg.get("to_user", ""), hint)
            return PlainTextResponse(content=reply_xml, media_type="application/xml")

        if is_new:
            # 刚完成映射，先欢迎，后台继续处理这条消息
            reply_xml = build_reply_xml(openid, msg.get("to_user", ""), f"{hint}\n\n收到，我想一下...")
        else:
            # 已映射用户，直接回复"收到"
            reply_xml = build_reply_xml(openid, msg.get("to_user", ""), "收到，我想一下...")

        logger.info(f"公众号消息: from={family_id}({openid}), content={content[:30]}")

        # 后台异步处理
        channel = MpChannel()
        background_tasks.add_task(process_text, family_id, content, channel=channel)
        return PlainTextResponse(content=reply_xml, media_type="application/xml")

    except Exception as e:
        logger.error(f"处理公众号消息异常: {e}", exc_info=True)
        return PlainTextResponse(content="success")


def _split_message(text: str, max_len: int = 600) -> list:
    """把长消息按段落拆分，优先保持段落完整"""
    if len(text) <= max_len:
        return [text]
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    segments = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) + 2 > max_len and current:
            segments.append(current.strip())
            current = p
        else:
            current = current + "\n\n" + p if current else p
    if current:
        segments.append(current.strip())
    # 还有超长的按句子硬切
    final = []
    for seg in segments:
        if len(seg) <= max_len:
            final.append(seg)
        else:
            for i in range(0, len(seg), max_len):
                final.append(seg[i:i + max_len])
    return final if final else [text[:max_len]]


def process_text(user_id: str, content: str, channel: MessageChannel = None):
    """处理文字消息，支持流式步进进度 + 超时兜底"""
    if channel is None:
        channel = WeComChannel()

    # 用户级串行锁：同一用户任务排队，避免并行冲突
    user_lock = _get_user_lock(user_id)
    if not user_lock.acquire(blocking=False):
        # 当前用户有任务在跑，发提示后退出
        _BUSY_HINTS = [
            "销销正在忙上一个任务呢，等这个做完了马上处理您的消息～",
            "上一个任务还没弄完，销销处理完就来看您这条～",
            "销销正在全力干上一个活儿，弄好了立刻回您～",
        ]
        channel.send_text(user_id, random.choice(_BUSY_HINTS))
        logger.info(f"用户 {user_id} 消息被排队: 当前有任务在跑")
        return

    try:
        _process_text_locked(user_id, content, channel)
    finally:
        user_lock.release()


def _process_text_locked(user_id: str, content: str, channel: MessageChannel):
    """process_text 的实际逻辑（已获取用户锁）"""
    # 超时兜底：任务超过 3 分钟未结束，主动安抚用户
    timeout_timer = None
    timeout_fired = {"fired": False}

    # 超时兜底话术池（随机轮换）
    _TIMEOUT_FALLBACK = [
        "这部分有点复杂，销销还在写，还需要几分钟～",
        "销销正在全力推进，请稍候～",
        "还在努力中，销销加油～",
        "稍等片刻哦，销销在加油干～",
        "销销还在埋头苦干呢～",
        "还在进行中，销销没闲着～",
        "请稍等，销销还在忙～",
        "销销在认真干活呢，请稍候～",
        "还在处理，销销没摸鱼～",
        "销销在努力推进中，稍等～",
    ]

    def _timeout_callback():
        timeout_fired["fired"] = True
        channel.send_text(user_id, random.choice(_TIMEOUT_FALLBACK))
        logger.info(f"超时兜底触发: user={user_id}, channel={channel.name}")

    try:
        user_name = get_user_name(user_id)

        # 进度推送话术池（随机轮换，避免重复）
        _START_RESUME = [
            "好的，销销接着刚才的继续干～",
            "来啦，销销接着刚才的活儿继续～",
            "没问题，销销继续，刚才的进度都记着呐～",
            "好嘞，销销续上刚才的，接着弄～",
            "收到，销销接上刚才的进度继续～",
            "来啦来啦，销销接着刚才的地方往下做～",
            "销销记得呢，接着刚才的继续～",
            "好哒，销销从刚才断掉的地方续上～",
            "收到，销销继续推进，刚才的成果都保存着～",
            "没问题，销销这就续上刚才的活儿～",
            "来啦，销销接着干，刚才到哪一步了都清楚～",
            "好嘞，销销继续刚才的任务，进度不丢～",
            "收到，销销接上进度继续往前推进～",
            "销销来啦，接着刚才的地方继续弄～",
            "好哒，销销续作开始，刚才的资料都在～",
            "收到收到，销销从刚才的断点恢复～",
            "没问题，销销继续，刚才做到哪都记着～",
            "来啦，销销接上刚才的继续加油干～",
            "好嘞，销销续上进度，继续往前冲～",
            "收到，销销接着刚才的成果继续推进～",
            "来啦，销销续上刚才的活儿继续干～",
        ]
        _HB_FALLBACK = [
            "销销还在忙～",
            "销销还在努力中，请稍等～",
            "还在处理中，销销没偷懒～",
            "稍等片刻哦，销销在加油干～",
            "销销还在埋头苦干呢～",
            "还在进行中，销销没闲着～",
            "销销正在全力推进，请稍候～",
            "还在努力中，销销加油～",
            "销销没停，还在处理中～",
            "请稍等，销销还在忙～",
            "销销在认真干活呢，请稍候～",
            "还在处理，销销没摸鱼～",
            "销销在努力推进中，稍等～",
            "还在加油干，销销不偷懒～",
            "销销正在全力处理，请稍等～",
            "还在忙活着，销销没停～",
            "销销在埋头处理，请稍候～",
            "还在努力中，销销持续推进～",
            "销销在认真干活，请稍等片刻～",
            "还在处理中，销销不松懈～",
            "销销在全力干活，请稍等哦～",
        ]

        def push_progress(event: AgentEvent):
            """事件驱动进度回调（直接消费标准 AgentEvent，避免企微刷屏）"""
            if event.type == AgentEventType.AGENT_START:
                if event.resumed:
                    channel.send_text(user_id, random.choice(_START_RESUME))

            elif event.type == AgentEventType.PLAN_CREATED:
                # 计划生成后向老人推送总体安排（只发一次）
                total = event.total or 0
                steps = event.steps_summary or []
                if total > 0:
                    msg = f"这个任务有点复杂，我要分 {total} 步来做：\n"
                    for i, desc in enumerate(steps[:5], 1):
                        msg += f"{i}. {desc}\n"
                    if len(steps) > 5:
                        msg += f"...还有 {len(steps) - 5} 步\n"
                    msg += "\n预计需要几分钟，您先忙别的，好了我叫您～"
                    channel.send_text(user_id, msg)

            elif event.type == AgentEventType.STEP_START:
                # 前 3 步开始时才发消息，避免刷屏
                if event.step and event.step <= 3:
                    channel.send_text(
                        user_id,
                        f"开始第 {event.step}/{event.total or '?'} 步：{event.description or '处理中'}..."
                    )

            elif event.type == AgentEventType.HEARTBEAT:
                channel.send_text(user_id, event.message or random.choice(_HB_FALLBACK))

            elif event.type == AgentEventType.TASK_CREATED:
                # 子任务创建时推送进度（根任务不推送）
                if event.parent_id and event.goal:
                    channel.send_text(
                        user_id,
                        f"销销拆分了一个子任务：{event.goal[:30]}..."
                    )

            elif event.type == AgentEventType.TASK_COMPLETED:
                if event.goal:
                    channel.send_text(user_id, f"✓ 销销完成了一个子任务～")

            elif event.type == AgentEventType.TASK_FAILED:
                if event.goal:
                    channel.send_text(user_id, f"⚠ 有个子任务没完成，销销在调整方案...")

        # 启动 3 分钟超时定时器
        timeout_timer = threading.Timer(180.0, _timeout_callback)
        timeout_timer.daemon = True
        timeout_timer.start()

        # 统一 Agent 执行入口（与 CLI 共用同一套核心逻辑）
        result = process_query(user_id, content, event_sink=push_progress)

        # 处理文件请求结果
        if result.get("is_file_request"):
            if result.get("found_files"):
                channel.send_text(user_id, result["reply"])
                logger.info(f"回复 {user_name}: 找到历史文件, channel={channel.name}")
                for fpath in result["found_files"][:3]:
                    try:
                        res = channel.send_file(user_id, str(fpath), title=os.path.basename(fpath))
                        if not res.get("err"):
                            logger.info(f"已推送历史文件 {user_name}: {fpath}")
                        else:
                            logger.warning(f"推送历史文件失败: {fpath}, err={res}")
                    except Exception as e:
                        logger.error(f"推送历史文件异常: {e}")
            else:
                channel.send_text(user_id, result["reply"])
                logger.info(f"回复 {user_name}: {result['reply'][:50]}..., channel={channel.name}")
            return

        reply = result["reply"]
        mode = result.get("mode", 1)
        files = result.get("files", [])

        # 任务完成，取消超时定时器
        if timeout_timer:
            timeout_timer.cancel()

        # 如果超时兜底已经触发，追加说明
        if timeout_fired["fired"]:
            reply = "终于弄好啦！\n\n" + reply

        if mode == 4:
            # 分段发送，间隔1.5秒避免刷屏
            segments = _split_message(reply, max_len=600)
            for seg in segments:
                channel.send_text(user_id, seg)
                time.sleep(1.5)
            logger.info(f"回复 {user_name}: 分段发送 {len(segments)} 条, channel={channel.name}")
        else:
            channel.send_text(user_id, reply)
            logger.info(f"回复 {user_name}: {reply[:50]}..., channel={channel.name}")

        # 推送生成的文件（PDF、图片等）
        if files:
            for fpath in files:
                try:
                    result = channel.send_file(user_id, fpath, title=os.path.basename(fpath))
                    if not result.get("err"):
                        logger.info(f"已推送文件 {user_name}: {fpath}")
                    else:
                        logger.warning(f"推送文件失败: {fpath}, err={result}")
                except Exception as e:
                    logger.error(f"推送文件异常: {e}")

        # 任务栈恢复：如果栈中还有挂起的任务，自动恢复执行
        from mind.interruption import has_suspended, pop_task
        if has_suspended(user_id):
            suspended = pop_task(user_id)
            if suspended:
                logger.info(f"自动恢复挂起任务: user={user_id}, query='{suspended.original_query}'")
                channel.send_text(user_id, f"对了，刚才您问的{suspended.original_query[:20]}...我还没说完呢，我继续做完～")
                # 重新触发 process_text 恢复执行
                # 注意：这里需要重新构造查询，让 Agent 从 checkpoint 恢复
                time.sleep(1)
                process_text(user_id, f"继续{suspended.original_query}")

    except Exception as e:
        if timeout_timer:
            timeout_timer.cancel()
        logger.error(f"处理文字消息失败 [{user_id}]: {e}", exc_info=True)
        channel.send_text(user_id, "销销出错了，请您稍后再试，或者联系管理员帮忙看看。")


def process_voice(user_id: str, media_id: str, msg_id: str, channel: MessageChannel = None):
    """处理语音消息：AMR -> WAV -> ASR -> 文本处理"""
    if channel is None:
        channel = WeComChannel()
    try:
        from mind.voice import amr_to_wav, transcribe_audio

        # 1. 下载语音文件
        amr_path = f"/tmp/voice_{msg_id}.amr"
        if not download_media(media_id, amr_path):
            channel.send_text(user_id, "销销没听清，您能再说一遍吗？")
            return

        # 2. AMR 转 WAV
        wav_path = f"/tmp/voice_{msg_id}.wav"
        if not amr_to_wav(amr_path, wav_path):
            channel.send_text(user_id, "销销处理语音格式时出了点问题，您打字说吧。")
            return

        # 3. 语音识别
        text = transcribe_audio(wav_path)
        if not text:
            channel.send_text(user_id, "销销没听清您说了什么，您能再说一遍吗？")
            return

        logger.info(f"语音识别结果 [{user_id}]: {text[:50]}...")

        # 4. 当作文本消息处理
        process_text(user_id, text, channel=channel)

        # 5. 清理临时文件
        for p in [amr_path, wav_path]:
            try:
                os.remove(p)
            except:
                pass

    except Exception as e:
        logger.error(f"处理语音消息失败 [{user_id}]: {e}", exc_info=True)
        channel.send_text(user_id, "销销处理语音时出了点问题，您打字说吧。")


def process_image(user_id: str, media_id: str, msg_id: str, channel: MessageChannel = None):
    """处理图片消息：下载 -> 压缩 -> vision 分析 -> 文本处理"""
    if channel is None:
        channel = WeComChannel()
    try:
        from mind.llm_client import chat

        # 1. 下载图片
        img_path = f"/tmp/image_{msg_id}.jpg"
        if not download_media(media_id, img_path):
            channel.send_text(user_id, "销销下载图片失败了，您能再发一次吗？")
            return

        # 2. 压缩图片（控制大小，加速传输）
        try:
            from PIL import Image
            img = Image.open(img_path)
            # 限制长边不超过 1024，减少 base64 体积
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            # 转为 RGB（去除透明通道兼容性）
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            compressed_path = f"/tmp/image_{msg_id}_compressed.jpg"
            img.save(compressed_path, "JPEG", quality=75)
            original_size = os.path.getsize(img_path)
            compressed_size = os.path.getsize(compressed_path)
            logger.info(f"图片压缩: {original_size/1024/1024:.1f}MB -> {compressed_size/1024/1024:.1f}MB")
            # 用压缩后的路径继续
            img_path = compressed_path
        except Exception as e:
            logger.warning(f"图片压缩失败，使用原图: {e}")

        # 3. 尝试 vision 分析（主模型）
        vision_result = ""
        try:
            system = "你是一位细心的图片分析助手。请仔细观察图片，提取其中的文字信息和关键内容，用中文简洁描述。如果是病历、化验单、处方等医疗文档，请提取所有可识别的指标、数值、诊断结论。"
            vision_result = chat(system, "请详细描述这张图片里的内容，特别是文字信息。", image_path=img_path, max_tokens=2048, temperature=0.3, model=os.getenv("MODEL_DAILY"))
        except Exception as e:
            logger.warning(f"主模型 vision 分析失败: {e}")

        # 4. 主模型失败，尝试本地 gemma-4-26b（若已加载且支持 vision）
        if not vision_result:
            try:
                local_model = os.getenv("LOCAL_MODEL_NAME", "gemma-4-26b-a4b-it-ud")
                vision_result = chat(system, "请详细描述这张图片里的内容，特别是文字信息。", image_path=img_path, max_tokens=2048, temperature=0.3, model=local_model)
                logger.info(f"本地模型 vision 分析成功")
            except Exception as e:
                logger.warning(f"本地模型 vision 分析也失败: {e}")

        # 5. 清理临时文件
        for p in [f"/tmp/image_{msg_id}.jpg", f"/tmp/image_{msg_id}_compressed.jpg"]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except:
                pass

        if vision_result:
            logger.info(f"图片分析结果 [{user_id}]: {vision_result[:100]}...")
            # vision 提取结果已很完整，直接回复避免本地 35B 模型 Agent 循环卡住
            reply = f"收到图片啦！我帮您看了，内容是这样的：\n\n{vision_result}\n\n您还有什么想了解的，随时跟我说～"
            # 企微限制单条 2048 字，超长则分段
            if len(reply) > 2000:
                parts = []
                while reply:
                    parts.append(reply[:2000])
                    reply = reply[2000:]
                for i, part in enumerate(parts):
                    channel.send_text(user_id, part)
            else:
                channel.send_text(user_id, reply)
        else:
            channel.send_text(user_id, "收到图片了！但我现在只能看懂文字，麻烦您把图片里的关键内容打字发给我，或者简单说说您想问什么～")

    except Exception as e:
        logger.error(f"处理图片消息失败 [{user_id}]: {e}", exc_info=True)
        channel.send_text(user_id, "销销处理图片时出了点问题，您把内容打字发给我吧。")


# ========== 健康检查 ==========
@app.get("/health")
def health():
    return {"status": "销销 running", "timestamp": int(time.time())}


# ========== 手动触发早播报（测试用）==========
@app.post("/trigger/morning")
def trigger_morning():
    from mind.companion_routines import morning_routine
    morning_routine()
    return {"status": "triggered"}


# ========== Web 聊天界面 ==========
@app.get("/api/users")
def list_users():
    """返回销售人员列表，供 Web 聊天选择身份"""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, name, role FROM user_profiles WHERE entity_type='sales' OR team_id IS NOT NULL ORDER BY role, name;")
            rows = cur.fetchall()
        conn.close()
        return [{"user_id": r[0], "name": r[1] or r[0], "role": r[2] or ""} for r in rows]
    except Exception as e:
        logger.warning(f"获取用户列表失败: {e}")
        return []


def _extract_task_title(result_path: Path) -> str:
    """从 result.md 中提取第一个 Markdown 标题作为任务名称"""
    if not result_path.exists():
        return ""
    try:
        text = result_path.read_text(encoding="utf-8")[:2000]
        for line in text.splitlines():
            line = line.strip()
            m = re.match(r"^#{1,3}\s*(.+)$", line)
            if m:
                title = m.group(1).strip()
                # 移除常见 emoji 前缀周围的空格，限制长度
                title = re.sub(r"\s+", " ", title)
                return title[:60]
    except Exception as e:
        logger.warning(f"提取任务标题失败: {e}")
    return ""


def _build_task_detail(work_dir: Path, timestamp: float = None) -> Optional[dict]:
    """从任务工作目录构建任务详情（供 latest_task 和 task_detail 复用）"""
    if not work_dir.exists():
        return None
    try:
        # 读取 checkpoint 中的 todos
        todos = []
        iteration = 0
        cp = work_dir / "checkpoint.json"
        if cp.exists():
            try:
                cp_data = json.loads(cp.read_text(encoding="utf-8"))
                todos = cp_data.get("todos", [])
                iteration = cp_data.get("iteration", 0)
            except Exception as e:
                logger.warning(f"读取 checkpoint 失败: {e}")

        # 结果展示：优先展示最有信息量的 .md 文件（报告正文），返回完整内容
        result_preview = ""
        result_file = work_dir / "result.md"
        if result_file.exists():
            result_preview = result_file.read_text(encoding="utf-8")

        # 如果 result.md 只是简短状态，尝试用工作目录中最大的 .md 文件作为展示源
        md_files = [p for p in work_dir.rglob("*.md") if p.is_file() and p.name != "result.md"]
        if len(result_preview.strip()) < 300 and md_files:
            md_files.sort(key=lambda p: p.stat().st_size, reverse=True)
            result_file = md_files[0]
            result_preview = result_file.read_text(encoding="utf-8")

        # 任务标题
        task_title = _extract_task_title(result_file) or work_dir.name

        # 产出文件
        files = []
        for p in work_dir.rglob("*"):
            if p.is_file() and p.stat().st_size > 100:
                suffix = p.suffix.lower()
                if suffix in (".pdf", ".png", ".jpg", ".jpeg", ".mp3", ".mp4", ".docx", ".pptx", ".md"):
                    rel = str(p.relative_to(DATA_DIR_ABS))
                    files.append({"name": p.name, "path": "/data/" + rel})

        # 对话记录
        conversation = ""
        conv_file = work_dir / "conversation.md"
        if conv_file.exists():
            conversation = conv_file.read_text(encoding="utf-8")[:5000]

        return {
            "id": work_dir.name,
            "title": task_title,
            "work_dir": str(work_dir),
            "todos": todos,
            "iteration": iteration,
            "result_preview": result_preview,
            "conversation": conversation,
            "files": files,
            "timestamp": timestamp,
        }
    except Exception as e:
        logger.warning(f"构建任务详情失败: {e}")
        return None


@app.get("/api/latest_task")
def latest_task(user_id: str = ""):
    """返回用户最近一次任务的目录、todo、产出文件和结果预览"""
    if not user_id:
        return {"task": None}
    state_file = Path(DATA_DIR_ABS) / "state" / "latest_task.json"
    if not state_file.exists():
        return {"task": None}
    try:
        mapping = json.loads(state_file.read_text(encoding="utf-8"))
        info = mapping.get(user_id)
        if not info:
            return {"task": None}
        work_dir = Path(info["work_dir"])
        if not work_dir.is_absolute():
            work_dir = DATA_DIR_ABS / work_dir
        detail = _build_task_detail(work_dir, timestamp=info.get("timestamp"))
        return {"task": detail}
    except Exception as e:
        logger.warning(f"获取 latest_task 失败: {e}")
        return {"task": None}


@app.get("/api/task_detail")
def task_detail(task_id: str = ""):
    """根据任务 ID 返回任务详情（用于点击任务历史）"""
    if not task_id:
        return {"task": None}
    work_dir = Path(DATA_DIR_ABS) / "tasks" / task_id
    if not work_dir.exists():
        return {"task": None}
    detail = _build_task_detail(work_dir)
    return {"task": detail}


@app.post("/api/delete_task")
def delete_task(task_id: str = ""):
    """删除指定任务的工作目录（用于任务历史删除）"""
    if not task_id:
        return {"success": False, "error": "缺少 task_id"}
    # 安全校验：只允许删除 data/tasks 下的目录，且 task_id 不能包含路径穿越
    if ".." in task_id or "/" in task_id or "\\" in task_id:
        return {"success": False, "error": "非法 task_id"}
    work_dir = Path(DATA_DIR_ABS) / "tasks" / task_id
    if not work_dir.exists() or not work_dir.is_dir():
        return {"success": False, "error": "任务不存在"}
    # 确保目录确实在 data/tasks 下，防止误删
    try:
        if not str(work_dir.resolve()).startswith(str(Path(DATA_DIR_ABS).resolve() / "tasks")):
            return {"success": False, "error": "非法路径"}
    except Exception:
        return {"success": False, "error": "路径解析失败"}
    try:
        import shutil
        shutil.rmtree(work_dir)
        return {"success": True}
    except Exception as e:
        logger.warning(f"删除任务失败: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/new_thread")
def new_thread(user_id: str = ""):
    """强制创建新对话线程，用于 Web UI 的'新建任务'按钮"""
    if not user_id:
        return {"success": False, "error": "缺少 user_id"}
    try:
        from mind.memory import Memory
        mem = Memory(user_id, "")
        thread_id = mem.new_thread(user_id)
        mem.close()
        return {"success": True, "thread_id": thread_id}
    except Exception as e:
        logger.warning(f"创建新线程失败: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/task_history")
def task_history(user_id: str = "", limit: int = 10):
    """返回用户最近的任务目录列表（附带中文标题）"""
    if not user_id:
        return {"tasks": []}
    tasks_dir = Path(DATA_DIR_ABS) / "tasks"
    if not tasks_dir.exists():
        return {"tasks": []}
    try:
        dirs = [d for d in tasks_dir.iterdir() if d.is_dir()]
        matched = []
        import re
        # 过滤掉 AgentSession 后台运行生成的内部工作目录：{user_id}_{timestamp}
        internal_pattern = re.compile(re.escape(user_id) + r"_\d+$")
        for d in dirs:
            # 跳过内部工作目录
            if internal_pattern.match(d.name):
                continue
            if user_id in d.name or d.name.startswith("x-"):
                cp = d / "checkpoint.json"
                mtime = cp.stat().st_mtime if cp.exists() else d.stat().st_mtime
                title = _extract_task_title(d / "result.md") or d.name
                matched.append({
                    "id": d.name,
                    "title": title,
                    "mtime": mtime,
                })
        matched.sort(key=lambda x: x["mtime"], reverse=True)
        return {"tasks": matched[:limit]}
    except Exception as e:
        logger.warning(f"获取 task_history 失败: {e}")
        return {"tasks": []}


WECHAT_KB_DIGEST_PATH = Path("third_party/wechat-digest-skill/output/digest.html")
WECHAT_KB_JSON_PATH = Path("third_party/wechat-digest-skill/output/knowledge_base.json")


def _load_kb_json():
    """读取 wechat-digest 知识库 JSON，失败时返回空结构。"""
    try:
        if WECHAT_KB_JSON_PATH.exists():
            return json.loads(WECHAT_KB_JSON_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取知识库 JSON 失败: {e}")
    return {"articles": {}, "topics": [], "tags": {}, "collections": {}, "leads": {}}


CHAT_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>销销 - 销售智能协作空间</title>
    <link rel="icon" href="/data/assets/xiaoxiaoshu-logo.png">
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;1,9..144,400&family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Serif+SC:wght@500;700;900&display=swap" rel="stylesheet" />
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            margin: 0;
            background: #f2f2f7;
            height: 100vh;
            overflow: hidden;
            color: #1c1c1e;
        }
        #app {
            display: flex;
            height: 100vh;
            width: 100vw;
        }
        /* 左侧边栏：项目和线程入口 */
        #sidebar-left {
            width: 260px;
            background: #ffffff;
            border-right: 1px solid #e5e5ea;
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
        }
        .sidebar-header {
            height: 58px;
            padding: 0 18px;
            border-bottom: 1px solid #e5e5ea;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .sidebar-title-block {
            display: flex;
            flex-direction: column;
            justify-content: center;
            gap: 1px;
        }
        .sidebar-title {
            font-family: var(--font-display);
            font-size: 17px;
            font-weight: 700;
            margin: 0;
            letter-spacing: -0.01em;
        }
        .sidebar-subtitle {
            font-size: 11px;
            color: #8e8e93;
            letter-spacing: 0.3px;
        }
        .logo {
            height: 32px;
            width: auto;
            display: block;
        }
        .logo-sm {
            height: 22px;
            width: auto;
            vertical-align: middle;
            margin-right: 8px;
        }
        .sidebar-section {
            padding: 16px 18px;
            border-bottom: 1px solid #e5e5ea;
        }
        .sidebar-section-title {
            font-size: 12px;
            font-weight: 600;
            color: #8e8e93;
            text-transform: uppercase;
            margin-bottom: 10px;
            letter-spacing: 0.5px;
        }
        #userSelect {
            width: 100%;
            padding: 8px 10px;
            border-radius: 8px;
            border: 1px solid #d1d1d6;
            background: #f9f9fb;
            font-size: 14px;
            outline: none;
        }
        .new-task-btn {
            width: 100%;
            padding: 10px 12px;
            border-radius: 8px;
            border: 1px dashed #007aff;
            background: #f0f7ff;
            color: #007aff;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
        }
        .new-task-btn:hover {
            background: #007aff;
            color: #fff;
            border-style: solid;
        }
        .task-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .task-item {
            padding: 10px 12px;
            border-radius: 8px;
            background: #f9f9fb;
            border: 1px solid #e5e5ea;
            font-size: 13px;
            cursor: pointer;
            transition: background 0.15s;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }
        .task-item:hover { background: #f0f0f5; }
        .task-item.active { background: #e5f2ff; border-color: #007aff; }
        .task-item-info { flex: 1; min-width: 0; }
        .task-item-title { font-weight: 600; color: #1c1c1e; margin-bottom: 2px; font-size: 13px; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
        .task-item-id { color: #8e8e93; font-size: 11px; margin-bottom: 2px; }
        .task-item-meta { color: #8e8e93; font-size: 11px; }
        .task-item-delete {
            width: 22px;
            height: 22px;
            border-radius: 6px;
            border: none;
            background: transparent;
            color: #8e8e93;
            font-size: 15px;
            line-height: 1;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        .task-item-delete:hover { background: #ff3b30; color: #fff; }
        .empty-state {
            font-size: 13px;
            color: #8e8e93;
            text-align: center;
            padding: 12px 0;
        }
        /* 中间主舞台 */
        #main {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-width: 0;
            background: #ffffff;
        }
        #chat-header {
            height: 58px;
            border-bottom: 1px solid #e5e5ea;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 24px;
            background: #ffffff;
        }
        #chat-header-title { font-weight: 600; font-size: 15px; }
        #connection-status {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            color: #8e8e93;
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #34c759;
        }
        .status-dot.connecting { background: #ffcc00; }
        .status-dot.disconnected { background: #ff3b30; }
        #chat {
            flex: 1;
            overflow-y: auto;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            background: #fafafc;
        }
        .msg {
            max-width: 78%;
            padding: 12px 16px;
            border-radius: 16px;
            line-height: 1.55;
            font-size: 15px;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .msg.user {
            align-self: flex-end;
            background: #007aff;
            color: #fff;
            border-bottom-right-radius: 4px;
        }
        .msg.agent {
            align-self: flex-start;
            background: #fff;
            color: #1c1c1e;
            border: 1px solid #e5e5ea;
            border-bottom-left-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }
        .msg.status {
            align-self: center;
            background: transparent;
            color: #8e8e93;
            font-size: 13px;
            padding: 4px 12px;
        }
        .msg.error {
            align-self: center;
            background: #ff3b30;
            color: #fff;
            font-size: 13px;
        }
        .msg a { color: inherit; text-decoration: underline; }
        .file-link {
            display: inline-block;
            margin-top: 8px;
            padding: 6px 10px;
            background: rgba(0,122,255,0.08);
            border-radius: 8px;
            font-size: 13px;
            text-decoration: none;
        }
        #input-area {
            background: #ffffff;
            border-top: 1px solid #e5e5ea;
            padding: 14px 24px 18px;
            display: flex;
            gap: 12px;
        }
        #quick-actions {
            display: flex;
            gap: 10px;
            padding: 10px 24px;
            background: #f9f9fb;
            border-top: 1px solid #e5e5ea;
            flex-wrap: wrap;
        }
        .quick-action {
            padding: 8px 14px;
            border: 1px solid #d1d1d6;
            border-radius: 16px;
            background: #ffffff;
            color: #333;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.15s;
        }
        .quick-action:hover {
            border-color: #007aff;
            color: #007aff;
            background: #f0f7ff;
        }
        .quick-action:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        #message {
            flex: 1;
            padding: 12px 16px;
            border: 1px solid #d1d1d6;
            border-radius: 22px;
            font-size: 15px;
            outline: none;
            transition: border-color 0.15s;
        }
        #message:focus { border-color: #007aff; }
        #message:disabled { background: #f2f2f7; }
        #send, #reconnect {
            padding: 12px 22px;
            border: none;
            border-radius: 22px;
            font-size: 15px;
            cursor: pointer;
        }
        #send { background: #007aff; color: #fff; }
        #send:disabled { background: #c7c7cc; cursor: not-allowed; }
        #stop {
            background: #ff3b30;
            color: #fff;
            border: none;
            padding: 12px 22px;
            border-radius: 22px;
            font-size: 15px;
            cursor: pointer;
        }
        #reconnect { background: #ff9500; color: #fff; display: none; }
        /* 右侧边栏：摘要和检查项 */
        #sidebar-right {
            width: 460px;
            background: #ffffff;
            border-left: 1px solid #e5e5ea;
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
        }
        .tabs {
            height: 58px;
            display: flex;
            align-items: center;
            border-bottom: 1px solid #e5e5ea;
        }
        .tab {
            flex: 1;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0;
            text-align: center;
            font-size: 14px;
            font-weight: 600;
            color: #8e8e93;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            transition: all 0.15s;
        }
        .tab.active {
            color: #007aff;
            border-bottom-color: #007aff;
        }
        .tab-content {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            display: none;
        }
        .tab-content.active { display: block; }
        .panel-section { margin-bottom: 24px; }
        .panel-section-title {
            font-size: 13px;
            font-weight: 600;
            color: #8e8e93;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .info-card {
            background: #f9f9fb;
            border: 1px solid #e5e5ea;
            border-radius: 10px;
            padding: 12px 14px;
            font-size: 13px;
            word-break: break-word;
        }
        .preview-content {
            background: #f9f9fb;
            border: 1px solid #e5e5ea;
            border-radius: 10px;
            padding: 14px 16px;
            font-size: 13px;
            line-height: 1.6;
            max-height: 60vh;
            overflow-y: auto;
        }
        .preview-content h1, .preview-content h2, .preview-content h3 {
            margin-top: 16px;
            margin-bottom: 8px;
            color: #1c1c1e;
        }
        .preview-content p { margin: 8px 0; }
        .preview-content ul, .preview-content ol {
            margin: 8px 0;
            padding-left: 20px;
        }
        .preview-content table {
            border-collapse: collapse;
            width: 100%;
            margin: 10px 0;
            font-size: 12px;
        }
        .preview-content th, .preview-content td {
            border: 1px solid #e5e5ea;
            padding: 6px 8px;
            text-align: left;
        }
        .preview-content th {
            background: #f2f2f7;
            font-weight: 600;
        }
        .preview-content blockquote {
            border-left: 3px solid #007aff;
            margin: 10px 0;
            padding-left: 12px;
            color: #555;
        }
        .preview-content code {
            background: #f2f2f7;
            padding: 2px 4px;
            border-radius: 4px;
            font-family: monospace;
        }
        .todo-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .todo-item {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 10px 12px;
            background: #f9f9fb;
            border-radius: 8px;
            font-size: 13px;
        }
        .todo-status {
            width: 16px;
            height: 16px;
            border-radius: 50%;
            border: 2px solid #d1d1d6;
            flex-shrink: 0;
            margin-top: 2px;
        }
        .todo-status.completed { background: #34c759; border-color: #34c759; }
        .todo-status.in_progress { background: #ffcc00; border-color: #ffcc00; }
        .todo-content.completed { text-decoration: line-through; color: #8e8e93; }
        .file-list { display: flex; flex-direction: column; gap: 8px; }
        .file-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 10px;
            background: #f9f9fb;
            border-radius: 8px;
            font-size: 13px;
        }
        .file-item a { color: #007aff; text-decoration: none; }
        .preview-content {
            background: #f9f9fb;
            border: 1px solid #e5e5ea;
            border-radius: 10px;
            padding: 14px 16px;
            font-size: 13px;
            line-height: 1.6;
            max-height: 60vh;
            overflow-y: auto;
        }
        .preview-content h1, .preview-content h2, .preview-content h3 {
            margin-top: 16px;
            margin-bottom: 8px;
            color: #1c1c1e;
        }
        .preview-content p { margin: 8px 0; }
        .preview-content ul, .preview-content ol {
            margin: 8px 0;
            padding-left: 20px;
        }
        .preview-content table {
            border-collapse: collapse;
            width: 100%;
            margin: 10px 0;
            font-size: 12px;
        }
        .preview-content th, .preview-content td {
            border: 1px solid #e5e5ea;
            padding: 6px 8px;
            text-align: left;
        }
        .preview-content th {
            background: #f2f2f7;
            font-weight: 600;
        }
        .preview-content blockquote {
            border-left: 3px solid #007aff;
            margin: 10px 0;
            padding-left: 12px;
            color: #555;
        }
        /* 面板系统 */
        #panels {
            flex: 1;
            display: flex;
            position: relative;
            overflow: hidden;
            min-width: 0;
        }
        .panel {
            display: none;
            flex: 1;
            min-width: 0;
            height: 100%;
            overflow: hidden;
        }
        .panel.active {
            display: flex;
        }
        #panel-collab {
            flex-direction: row;
        }
        #panel-news, #panel-project {
            flex-direction: column;
            background: #fff;
        }
        #panel-news iframe, #panel-project iframe {
            width: 100%;
            height: 100%;
            border: none;
        }
        .panel-empty {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: #8e8e93;
            font-size: 16px;
        }

        /* 左侧看板切换 */
        .panel-nav {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 12px;
        }
        .panel-nav-btn {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid transparent;
            background: #f9f9fb;
            color: #1c1c1e;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
            text-align: left;
        }
        .panel-nav-btn:hover {
            background: #f0f0f5;
        }
        .panel-nav-btn.active {
            background: #e5f2ff;
            border-color: #007aff;
            color: #007aff;
        }
        .panel-nav-icon {
            width: 22px;
            height: 22px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
        }

        /* 悬浮聊天窗口 */
        #floating-chat {
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 380px;
            height: 520px;
            background: #ffffff;
            border-radius: 16px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.18);
            display: none;
            flex-direction: column;
            overflow: hidden;
            z-index: 1000;
            border: 1px solid #e5e5ea;
        }
        #floating-chat.open {
            display: flex;
        }
        #floating-chat-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 14px;
            background: #f9f9fb;
            border-bottom: 1px solid #e5e5ea;
        }
        #floating-chat-title {
            display: flex;
            align-items: center;
            gap: 8px;
            font-weight: 600;
            font-size: 14px;
        }
        #floating-chat-title img {
            height: 22px;
            width: auto;
        }
        #floating-chat-close {
            background: none;
            border: none;
            font-size: 18px;
            cursor: pointer;
            color: #8e8e93;
        }
        #floating-chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
            background: #f2f2f7;
        }
        #floating-chat-input-area {
            display: flex;
            padding: 10px;
            border-top: 1px solid #e5e5ea;
            gap: 8px;
            background: #fff;
        }
        #floating-chat-input {
            flex: 1;
            padding: 8px 10px;
            border: 1px solid #d1d1d6;
            border-radius: 18px;
            outline: none;
            font-size: 14px;
        }
        #floating-chat-send {
            padding: 8px 14px;
            border: none;
            border-radius: 18px;
            background: #007aff;
            color: #fff;
            font-size: 14px;
            cursor: pointer;
        }
        #floating-chat-toggle {
            position: fixed;
            bottom: 24px;
            right: 24px;
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: #007aff;
            color: #fff;
            border: none;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
            cursor: pointer;
            z-index: 1000;
            display: none;
            align-items: center;
            justify-content: center;
            font-size: 26px;
        }
        #floating-chat-toggle.open {
            display: flex;
        }
        #floating-chat-toggle .badge {
            position: absolute;
            top: -2px;
            right: -2px;
            width: 18px;
            height: 18px;
            background: #ff3b30;
            border-radius: 50%;
            font-size: 11px;
            display: none;
            align-items: center;
            justify-content: center;
        }
        .mini-msg {
            margin-bottom: 10px;
            max-width: 85%;
            padding: 8px 12px;
            border-radius: 14px;
            font-size: 13px;
            line-height: 1.4;
            word-break: break-word;
        }
        .mini-msg.user {
            background: #007aff;
            color: #fff;
            margin-left: auto;
            border-bottom-right-radius: 4px;
        }
        .mini-msg.agent {
            background: #fff;
            color: #1c1c1e;
            border-bottom-left-radius: 4px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.08);
        }
        .mini-msg.status {
            background: transparent;
            color: #8e8e93;
            font-size: 12px;
            text-align: center;
            max-width: 100%;
        }

        /* ========== 统一资讯台配色与字体 ========== */
        :root {
            --paper:#f4efe4; --paper-2:#efe8d8; --card:#fbf8f0; --ink:#1c1813; --ink-2:#4a4239; --ink-3:#7a6f5e;
            --line:#d9cfb8; --line-2:#e7dfcc; --vermilion:#d8401f; --vermilion-d:#b8311a; --moss:#41684e; --gold:#a9762a;
            --font-display:"Fraunces","Noto Serif SC",Georgia,serif;
            --font-sans:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
            --font-mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
        }
        body { background: var(--paper) !important; color: var(--ink) !important; font-family: var(--font-sans) !important; }
        #app { background: var(--paper) !important; }
        #sidebar-left { background: var(--paper-2) !important; border-color: var(--line) !important; }
        #sidebar-right, .panel { background: var(--card) !important; border-color: var(--line) !important; }
        #main { background: var(--paper) !important; border-color: var(--line) !important; }
        .sidebar-header { background: transparent !important; border-color: var(--line) !important; }
        #chat-header { background: var(--card) !important; border-color: var(--line) !important; }
        .tabs { background: var(--card) !important; border-color: var(--line) !important; }
        .tab { color: var(--ink-3) !important; }
        .sidebar-header, .sidebar-section, .panel-section, .info-card, .file-list, .preview-content, .task-item,
        #chat, .msg.agent, .mini-msg.agent {
            background: var(--paper-2) !important; border-color: var(--line) !important; color: var(--ink) !important;
        }
        .msg.user, .mini-msg.user, .new-task-btn, #send, #floating-chat-send {
            background: var(--vermilion) !important; color: var(--paper) !important; border-color: var(--vermilion-d) !important;
        }
        .new-task-btn:hover, #send:hover, #floating-chat-send:hover {
            background: var(--vermilion-d) !important;
        }
        .quick-action {
            background: var(--card) !important; color: var(--ink-2) !important; border: 1px solid var(--line) !important;
        }
        .quick-action:hover {
            background: var(--vermilion) !important; color: var(--paper) !important; border-color: var(--vermilion-d) !important;
        }
        .msg.agent, .mini-msg.agent {
            background: var(--card) !important; border-color: var(--line) !important; color: var(--ink) !important;
            box-shadow: 0 1px 4px rgba(28,24,19,0.06) !important;
        }
        .info-card, .preview-content, .file-item, .todo-item {
            background: var(--card) !important; border-color: var(--line) !important;
            box-shadow: 0 1px 4px rgba(28,24,19,0.04) !important;
        }
        #chat { background: var(--paper) !important; }
        #chat-header-title { font-family: var(--font-display) !important; font-size: 16px !important; color: var(--ink) !important; }
        #quick-actions { background: var(--paper) !important; border-color: var(--line) !important; }
        #input-area { background: var(--card) !important; border-color: var(--line) !important; }
        .sidebar-section-title { color: var(--ink-3) !important; font-family: var(--font-sans) !important; }
        .panel-section-title { color: var(--ink-3) !important; }
        .task-item { background: var(--paper-2) !important; border-color: var(--line) !important; }
        .task-item:hover { background: var(--paper) !important; }
        .task-item.active { background: rgba(216,64,31,0.08) !important; border-color: var(--vermilion) !important; }
        .panel-nav-btn { background: var(--paper-2) !important; border-color: transparent !important; color: var(--ink-2) !important; }
        .panel-nav-btn:hover { background: var(--paper) !important; }
        .panel-nav-btn.active { background: var(--card) !important; border-color: var(--vermilion) !important; color: var(--vermilion) !important; }
        a, a.file-link, .file-item a { color: var(--vermilion) !important; }
        #chat-header-title, .sidebar-title, .panel-section-title, .sidebar-section-title, .task-item-title, .section-title {
            font-family: var(--font-display) !important; color: var(--ink) !important;
        }
        .status-dot.connected { background: var(--moss) !important; }
        .status-dot.connecting { background: var(--gold) !important; }
        .status-dot.disconnected { background: var(--vermilion) !important; }
        #message, #floating-chat-input { background: var(--paper) !important; color: var(--ink) !important; border-color: var(--line) !important; }
        #floating-chat { background: var(--card) !important; border-color: var(--line) !important; }
        #floating-chat-header { background: var(--paper-2) !important; border-color: var(--line) !important; }
        #floating-chat-toggle { background: var(--vermilion) !important; }
        .tab.active { border-bottom-color: var(--vermilion) !important; color: var(--vermilion) !important; }
        .tab:hover { color: var(--vermilion-d) !important; }
        .empty-state { color: var(--ink-3) !important; }
        .todo-status.completed { background: var(--moss) !important; }
        .todo-status.in_progress { background: var(--gold) !important; }
    </style>
</head>
<body>
    <div id="app">
        <!-- 左侧：项目和线程入口 -->
        <aside id="sidebar-left">
            <div class="sidebar-header">
                <img src="/data/assets/xiaoxiaoshu-logo.png" alt="销销树" class="logo">
                <div class="sidebar-title-block">
                    <div class="sidebar-title">销销</div>
                    <div class="sidebar-subtitle">销售智能协作空间</div>
                </div>
            </div>
            <div class="sidebar-section">
                <div class="panel-nav">
                    <button class="panel-nav-btn active" data-panel="collab">
                        <span class="panel-nav-icon">💬</span>
                        <span>协作看板</span>
                    </button>
                    <button class="panel-nav-btn" data-panel="news">
                        <span class="panel-nav-icon">📚</span>
                        <span>资讯看板</span>
                    </button>
                    <button class="panel-nav-btn" data-panel="project">
                        <span class="panel-nav-icon">📁</span>
                        <span>项目看板</span>
                    </button>
                </div>
            </div>
            <div class="sidebar-section">
                <div class="sidebar-section-title">销售人员</div>
                <select id="userSelect"></select>
            </div>
            <div class="sidebar-section">
                <button id="newTaskBtn" class="new-task-btn">+ 新建任务</button>
            </div>
            <div class="sidebar-section">
                <div class="sidebar-section-title">当前任务</div>
                <div id="currentTaskPanel" class="empty-state">暂无任务</div>
            </div>
            <div class="sidebar-section" style="flex:1; overflow-y:auto;">
                <div class="sidebar-section-title">任务历史</div>
                <div id="taskHistoryPanel" class="task-list"></div>
            </div>
        </aside>

        <!-- 右侧面板区 -->
        <div id="panels">
            <!-- 协作看板：原聊天 + Summary -->
            <div id="panel-collab" class="panel active">
                <main id="main">
                    <div id="chat-header">
                        <div id="chat-header-title"><img src="/data/assets/xiaoxiaoshu-logo.png" alt="销销树" class="logo-sm">与销销协作</div>
                        <div id="connection-status">
                            <span class="status-dot connecting" id="statusDot"></span>
                            <span id="statusText">连接中…</span>
                        </div>
                    </div>
                    <div id="chat"></div>
                    <div id="quick-actions">
                        <button class="quick-action" data-prompt="研究一下 【公司名】，输出客户纵横分析">客户研究</button>
                        <button class="quick-action" data-prompt="开始销售教练：你来当客户，模拟一次上门拜访">销售教练</button>
                        <button class="quick-action" data-prompt="给 【公司名】 出一版整合营销方案">营销方案</button>
                    </div>
                    <div id="input-area">
                        <input type="text" id="message" placeholder="等待连接…" autocomplete="off" disabled>
                        <button id="send" disabled>发送</button>
                        <button id="stop" style="display:none;">停止</button>
                        <button id="reconnect">重连</button>
                    </div>
                </main>

                <aside id="sidebar-right">
                    <div class="tabs">
                        <div class="tab active" data-tab="summary">Summary</div>
                        <div class="tab" data-tab="conversation">对话</div>
                        <div class="tab" data-tab="checklist">Checklist</div>
                    </div>
                    <div id="tab-summary" class="tab-content active">
                        <div class="panel-section">
                            <div class="panel-section-title">任务状态</div>
                            <div id="summaryStatus" class="info-card">等待任务开始…</div>
                        </div>
                        <div class="panel-section">
                            <div class="panel-section-title">产出文件</div>
                            <div id="summaryFiles" class="file-list"></div>
                        </div>
                        <div class="panel-section">
                            <div class="panel-section-title">完整结果</div>
                            <div id="summaryPreview" class="preview-content">暂无结果</div>
                        </div>
                    </div>
                    <div id="tab-conversation" class="tab-content">
                        <div class="panel-section">
                            <div class="panel-section-title">对话记录</div>
                            <div id="conversationContent" class="preview-content">暂无对话记录</div>
                        </div>
                    </div>
                    <div id="tab-checklist" class="tab-content">
                        <div class="panel-section">
                            <div class="panel-section-title">执行检查项</div>
                            <div id="checklistItems" class="todo-list"></div>
                        </div>
                    </div>
                </aside>
            </div>

            <!-- 资讯看板 -->
            <div id="panel-news" class="panel">
                <iframe src="/wechat_kb" title="行业资讯库"></iframe>
            </div>

            <!-- 项目看板 -->
            <div id="panel-project" class="panel">
                <div class="panel-empty">
                    <div>项目看板占位中<br><small>具体内容稍后配置</small></div>
                </div>
            </div>
        </div>
    </div>

    <!-- 悬浮聊天窗口 -->
    <div id="floating-chat">
        <div id="floating-chat-header">
            <div id="floating-chat-title"><img src="/data/assets/xiaoxiaoshu-logo.png" alt="销销">销销</div>
            <button id="floating-chat-close">−</button>
        </div>
        <div id="floating-chat-messages"></div>
        <div id="floating-chat-input-area">
            <input type="text" id="floating-chat-input" placeholder="跟销销说点什么…" autocomplete="off">
            <button id="floating-chat-send">发送</button>
        </div>
    </div>
    <button id="floating-chat-toggle" title="和销销聊天">
        🤖
        <span class="badge" id="floating-chat-badge">0</span>
    </button>

    <script>
        const chat = document.getElementById("chat");
        const messageInput = document.getElementById("message");
        const sendBtn = document.getElementById("send");
        const reconnectBtn = document.getElementById("reconnect");
        const userSelect = document.getElementById("userSelect");
        const newTaskBtn = document.getElementById("newTaskBtn");
        const statusDot = document.getElementById("statusDot");
        const statusText = document.getElementById("statusText");
        const currentTaskPanel = document.getElementById("currentTaskPanel");
        const taskHistoryPanel = document.getElementById("taskHistoryPanel");
        const summaryStatus = document.getElementById("summaryStatus");
        const summaryFiles = document.getElementById("summaryFiles");
        const summaryPreview = document.getElementById("summaryPreview");
        const conversationContent = document.getElementById("conversationContent");
        const checklistItems = document.getElementById("checklistItems");
        const stopBtn = document.getElementById("stop");

        // 外部入口（如资讯工作台）通过 /chat?prompt=... 预填输入框
        (function initPromptFromUrl() {
            const params = new URLSearchParams(window.location.search);
            const prompt = params.get("prompt");
            if (prompt) {
                messageInput.value = prompt;
                messageInput.focus();
                // 移除 prompt 参数，避免刷新时重复填充
                const url = new URL(window.location.href);
                url.searchParams.delete("prompt");
                window.history.replaceState({}, "", url.toString());
            }
        })();

        // 用户手动点击“新建任务”后，短暂跳过轮询，避免旧任务重新覆盖空状态
        let manualNewTask = false;

        function append(text, cls = "agent") {
            const div = document.createElement("div");
            div.className = "msg " + cls;
            div.innerHTML = text;
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
            return div;
        }

        function escapeHtml(text) {
            const div = document.createElement("div");
            div.textContent = text;
            return div.innerHTML;
        }

        function formatTime(ts) {
            if (!ts) return "";
            const d = new Date(ts * 1000);
            return `${d.getHours().toString().padStart(2,"0")}:${d.getMinutes().toString().padStart(2,"0")}`;
        }

        async function loadUsers() {
            try {
                const res = await fetch("/api/users");
                if (!res.ok) throw new Error("加载用户失败");
                const users = await res.json();
                userSelect.innerHTML = "";
                if (users.length === 0) {
                    const opt = document.createElement("option");
                    opt.value = "web_user";
                    opt.textContent = "默认用户";
                    userSelect.appendChild(opt);
                    return;
                }
                users.forEach(u => {
                    const opt = document.createElement("option");
                    opt.value = u.user_id;
                    opt.textContent = `${u.name} (${u.user_id})`;
                    userSelect.appendChild(opt);
                });
            } catch (e) {
                console.error(e);
                append("加载销售人员失败，请刷新页面重试", "error");
            }
        }

        function setConnectionStatus(state) {
            statusDot.className = "status-dot " + state;
            if (state === "connected") {
                statusText.textContent = "已连接";
                sendBtn.disabled = false;
                messageInput.disabled = false;
                messageInput.placeholder = "跟销销说点什么…";
                reconnectBtn.style.display = "none";
            } else if (state === "connecting") {
                statusText.textContent = "连接中…";
                sendBtn.disabled = true;
                messageInput.disabled = true;
                messageInput.placeholder = "等待连接…";
                reconnectBtn.style.display = "none";
            } else {
                statusText.textContent = "已断开";
                sendBtn.disabled = true;
                messageInput.disabled = true;
                messageInput.placeholder = "连接已断开…";
                reconnectBtn.style.display = "inline-block";
            }
        }

        function renderCurrentTask(task) {
            if (!task) {
                currentTaskPanel.innerHTML = '<div class="empty-state">暂无任务</div>';
                summaryStatus.textContent = "等待任务开始…";
                summaryFiles.innerHTML = "";
                summaryPreview.textContent = "暂无结果";
                conversationContent.textContent = "暂无对话记录";
                checklistItems.innerHTML = "";
                return;
            }
            currentTaskPanel.innerHTML = `
                <div class="task-item active">
                    <div class="task-item-title">${escapeHtml(task.title || task.id)}</div>
                    <div class="task-item-id">${escapeHtml(task.id)}</div>
                    <div class="task-item-meta">迭代 ${task.iteration} · ${formatTime(task.timestamp)}</div>
                </div>
            `;
            summaryStatus.textContent = `${task.title || task.id} · 迭代 ${task.iteration}`;

            summaryFiles.innerHTML = "";
            if (task.files && task.files.length > 0) {
                task.files.forEach(f => {
                    summaryFiles.innerHTML += `
                        <div class="file-item">📎 <a href="${f.path}" target="_blank">${f.name}</a></div>
                    `;
                });
            } else {
                summaryFiles.innerHTML = '<div class="empty-state">暂无文件</div>';
            }

            summaryPreview.innerHTML = task.result_preview
                ? (typeof marked !== 'undefined' ? marked.parse(task.result_preview) : escapeHtml(task.result_preview).replace(/\\n/g, "<br>"))
                : "暂无结果";

            conversationContent.innerHTML = task.conversation
                ? (typeof marked !== 'undefined' ? marked.parse(task.conversation) : escapeHtml(task.conversation).replace(/\\n/g, "<br>"))
                : "暂无对话记录";

            checklistItems.innerHTML = "";
            if (task.todos && task.todos.length > 0) {
                task.todos.forEach(t => {
                    const statusClass = t.status === "completed" ? "completed" : (t.status === "in_progress" ? "in_progress" : "");
                    const contentClass = t.status === "completed" ? "completed" : "";
                    checklistItems.innerHTML += `
                        <div class="todo-item">
                            <div class="todo-status ${statusClass}"></div>
                            <div class="todo-content ${contentClass}">${t.content}</div>
                        </div>
                    `;
                });
            } else {
                checklistItems.innerHTML = '<div class="empty-state">暂无检查项</div>';
            }
        }

        async function pollTask() {
            if (manualNewTask) return;
            const userId = userSelect.value || "web_user";
            try {
                const res = await fetch(`/api/latest_task?user_id=${userId}`);
                if (!res.ok) return;
                const data = await res.json();
                renderCurrentTask(data.task);
            } catch (e) {
                console.error("拉取任务状态失败:", e);
            }
        }

        async function loadTaskHistory() {
            const userId = userSelect.value || "web_user";
            try {
                const res = await fetch(`/api/task_history?user_id=${userId}&limit=10`);
                if (!res.ok) return;
                const data = await res.json();
                taskHistoryPanel.innerHTML = "";
                if (!data.tasks || data.tasks.length === 0) {
                    taskHistoryPanel.innerHTML = '<div class="empty-state">暂无历史</div>';
                    return;
                }
                data.tasks.forEach(t => {
                    const item = document.createElement("div");
                    item.className = "task-item";
                    item.title = escapeHtml(t.title || t.id);
                    item.innerHTML = `
                        <div class="task-item-info">
                            <div class="task-item-title">${escapeHtml(t.title || t.id)}</div>
                            <div class="task-item-id">${escapeHtml(t.id)}</div>
                            <div class="task-item-meta">${formatTime(t.mtime)}</div>
                        </div>
                        <button class="task-item-delete" title="删除">×</button>
                    `;
                    item.querySelector(".task-item-info").addEventListener("click", () => loadTaskDetail(t.id));
                    item.querySelector(".task-item-delete").addEventListener("click", (e) => {
                        e.stopPropagation();
                        deleteTask(t.id);
                    });
                    taskHistoryPanel.appendChild(item);
                });
            } catch (e) {
                console.error("加载任务历史失败:", e);
            }
        }

        async function deleteTask(taskId) {
            if (!confirm(`确定要删除任务 ${taskId} 吗？\n删除后将无法恢复。`)) return;
            try {
                const res = await fetch(`/api/delete_task?task_id=${taskId}`, { method: "POST" });
                if (!res.ok) return;
                const data = await res.json();
                if (data.success) {
                    loadTaskHistory();
                    // 如果删除的是当前展示的任务，清空右侧面板
                    if (summaryStatus.textContent.includes(taskId)) {
                        renderCurrentTask(null);
                    }
                } else {
                    alert("删除失败：" + (data.error || "未知错误"));
                }
            } catch (e) {
                console.error("删除任务失败:", e);
            }
        }

        async function loadTaskDetail(taskId) {
            try {
                const res = await fetch(`/api/task_detail?task_id=${taskId}`);
                if (!res.ok) return;
                const data = await res.json();
                renderCurrentTask(data.task);
                // 高亮当前选中的历史任务
                document.querySelectorAll("#taskHistoryPanel .task-item").forEach(el => el.classList.remove("active"));
                const items = document.querySelectorAll("#taskHistoryPanel .task-item");
                items.forEach(el => {
                    if (el.querySelector(".task-item-id")?.textContent === taskId) {
                        el.classList.add("active");
                    }
                });
            } catch (e) {
                console.error("加载任务详情失败:", e);
            }
        }

        let ws = null;
        let currentStatus = null;
        let reconnectTimer = null;
        let streamingMsg = null;

        function connect() {
            setConnectionStatus("connecting");
            const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
            ws = new WebSocket(`${wsProtocol}//${location.host}/ws/chat`);

            ws.onopen = () => {
                setConnectionStatus("connected");
                messageInput.focus();
            };

            ws.onclose = () => {
                setConnectionStatus("disconnected");
                if (!reconnectTimer) {
                    reconnectTimer = setTimeout(() => {
                        reconnectTimer = null;
                        connect();
                    }, 3000);
                }
            };

            ws.onerror = (err) => {
                console.error("WebSocket error:", err);
                setConnectionStatus("disconnected");
            };

            function ensureStreamingAgent() {
                if (currentStatus) {
                    currentStatus.remove();
                    currentStatus = null;
                }
                if (!streamingMsg) {
                    streamingMsg = document.createElement("div");
                    streamingMsg.className = "msg agent";
                    streamingMsg.style.whiteSpace = "pre-wrap";
                    chat.appendChild(streamingMsg);
                    chat.scrollTop = chat.scrollHeight;
                }
                return streamingMsg;
            }

            ws.onmessage = (ev) => {
                const data = JSON.parse(ev.data);
                if (data.type === "status") {
                    if (!currentStatus) currentStatus = append(data.message, "status");
                    else currentStatus.textContent = data.message;
                } else if (data.type === "event") {
                    if (!currentStatus) currentStatus = append(data.message || "处理中…", "status");
                    else currentStatus.textContent = data.message || "处理中…";
                } else if (data.type === "token") {
                    ensureStreamingAgent().textContent += data.content;
                    chat.scrollTop = chat.scrollHeight;
                } else if (data.type === "result") {
                    if (currentStatus) { currentStatus.remove(); currentStatus = null; }
                    let html = escapeHtml(data.reply).replace(/\\n/g, "<br>");
                    (data.files || []).forEach(f => {
                        const href = f.startsWith("/") ? f : "/" + f;
                        html += `<a class="file-link" href="${href}" target="_blank">📎 ${f.split("/").pop()}</a>`;
                    });
                    if ((data.found_files || []).length) {
                        html += "<br><strong>找到的历史文件：</strong>";
                        data.found_files.forEach(f => {
                            const href = f.startsWith("/") ? f : "/" + f;
                            html += `<a class="file-link" href="${href}" target="_blank">📎 ${f.split("/").pop()}</a>`;
                        });
                    }
                    if (streamingMsg) {
                        streamingMsg.innerHTML = html;
                        streamingMsg = null;
                    } else {
                        append(html, "agent");
                    }
                    finishTurn();
                    pollTask();
                } else if (data.type === "error") {
                    if (currentStatus) { currentStatus.remove(); currentStatus = null; }
                    if (streamingMsg) { streamingMsg.remove(); streamingMsg = null; }
                    append(data.message, "error");
                    finishTurn();
                }
            };
        }

        function send() {
            const text = messageInput.value.trim();
            if (!text) return;
            if (!ws || ws.readyState !== WebSocket.OPEN) {
                append("尚未连接到服务器，请稍等或点击重连", "error");
                return;
            }
            manualNewTask = false;
            append(escapeHtml(text).replace(/\\n/g, "<br>"), "user");
            messageInput.value = "";
            sendBtn.style.display = "none";
            stopBtn.style.display = "inline-block";
            messageInput.disabled = true;
            streamingMsg = null;
            currentStatus = append("销销正在思考…", "status");
            ws.send(JSON.stringify({ user_id: userSelect.value, message: text }));
        }

        function finishTurn() {
            sendBtn.style.display = "inline-block";
            stopBtn.style.display = "none";
            stopBtn.disabled = false;
            messageInput.disabled = false;
            sendBtn.disabled = false;
            messageInput.focus();
        }

        sendBtn.addEventListener("click", send);
        messageInput.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
        stopBtn.addEventListener("click", () => {
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            ws.send(JSON.stringify({ user_id: userSelect.value, action: "stop" }));
            stopBtn.disabled = true;
        });
        reconnectBtn.addEventListener("click", () => {
            if (reconnectTimer) clearTimeout(reconnectTimer);
            reconnectTimer = null;
            connect();
        });

        document.querySelectorAll(".quick-action").forEach(btn => {
            btn.addEventListener("click", () => {
                if (!ws || ws.readyState !== WebSocket.OPEN) return;
                const prompt = btn.getAttribute("data-prompt");
                messageInput.value = prompt;
                messageInput.focus();
            });
        });

        newTaskBtn.addEventListener("click", async () => {
            const userId = userSelect.value || "web_user";
            try {
                const res = await fetch(`/api/new_thread?user_id=${userId}`, { method: "POST" });
                if (!res.ok) return;
                const data = await res.json();
                if (data.success) {
                    manualNewTask = true;
                    // 清空聊天区域
                    chat.innerHTML = `
                        <div class="msg agent">
                            <div class="message-content">已新建任务，请直接输入你想研究/处理的内容，比如："研究一下快手公司"或"帮我写一封跟进邮件"</div>
                        </div>
                    `;
                    // 清空当前任务展示
                    renderCurrentTask(null);
                } else {
                    alert("新建任务失败：" + (data.error || "未知错误"));
                }
            } catch (e) {
                console.error("新建任务失败:", e);
            }
        });

        document.querySelectorAll(".tab").forEach(tab => {
            tab.addEventListener("click", () => {
                document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
                document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
                tab.classList.add("active");
                document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
            });
        });

        userSelect.addEventListener("change", () => { pollTask(); loadTaskHistory(); });

        // ========== 面板切换 ==========
        const panels = document.querySelectorAll(".panel");
        const panelNavBtns = document.querySelectorAll(".panel-nav-btn");
        const floatingChatEl = document.getElementById("floating-chat");
        const floatingChatToggle = document.getElementById("floating-chat-toggle");
        const floatingChatClose = document.getElementById("floating-chat-close");
        const floatingChatMessages = document.getElementById("floating-chat-messages");
        const floatingChatInput = document.getElementById("floating-chat-input");
        const floatingChatSend = document.getElementById("floating-chat-send");
        const floatingChatBadge = document.getElementById("floating-chat-badge");

        function switchPanel(name) {
            panels.forEach(p => p.classList.remove("active"));
            panelNavBtns.forEach(b => b.classList.remove("active"));
            document.getElementById("panel-" + name).classList.add("active");
            document.querySelector(`.panel-nav-btn[data-panel="${name}"]`).classList.add("active");

            if (name === "collab") {
                floatingChatToggle.classList.remove("open");
                floatingChatEl.classList.remove("open");
            } else {
                floatingChatToggle.classList.add("open");
            }
        }

        panelNavBtns.forEach(btn => {
            btn.addEventListener("click", () => switchPanel(btn.dataset.panel));
        });

        // ========== 悬浮聊天窗口 ==========
        let wsFloat = null;
        let floatCurrentStatus = null;
        let floatStreamingMsg = null;
        let floatReconnectTimer = null;
        let floatUnread = 0;

        function appendMini(text, cls = "agent") {
            const div = document.createElement("div");
            div.className = "mini-msg " + cls;
            div.innerHTML = text;
            floatingChatMessages.appendChild(div);
            floatingChatMessages.scrollTop = floatingChatMessages.scrollHeight;
            return div;
        }

        function updateFloatBadge() {
            if (floatUnread > 0 && !floatingChatEl.classList.contains("open")) {
                floatingChatBadge.textContent = floatUnread > 99 ? "99+" : floatUnread;
                floatingChatBadge.style.display = "flex";
            } else {
                floatingChatBadge.style.display = "none";
                floatUnread = 0;
            }
        }

        function finishFloatTurn() {
            floatingChatSend.disabled = false;
            floatingChatInput.disabled = false;
            floatingChatInput.focus();
        }

        function connectFloatingChat() {
            const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
            wsFloat = new WebSocket(`${wsProtocol}//${location.host}/ws/chat`);

            wsFloat.onopen = () => {
                appendMini("已连接，可以继续聊天", "status");
            };

            wsFloat.onclose = () => {
                appendMini("连接已断开，正在重连…", "status");
                if (!floatReconnectTimer) {
                    floatReconnectTimer = setTimeout(() => {
                        floatReconnectTimer = null;
                        connectFloatingChat();
                    }, 3000);
                }
            };

            wsFloat.onerror = (err) => {
                console.error("悬浮聊天 WebSocket 错误:", err);
            };

            function ensureFloatStreamingAgent() {
                if (floatCurrentStatus) {
                    floatCurrentStatus.remove();
                    floatCurrentStatus = null;
                }
                if (!floatStreamingMsg) {
                    floatStreamingMsg = document.createElement("div");
                    floatStreamingMsg.className = "mini-msg agent";
                    floatStreamingMsg.style.whiteSpace = "pre-wrap";
                    floatingChatMessages.appendChild(floatStreamingMsg);
                    floatingChatMessages.scrollTop = floatingChatMessages.scrollHeight;
                }
                return floatStreamingMsg;
            }

            wsFloat.onmessage = (ev) => {
                const data = JSON.parse(ev.data);
                if (data.type === "status" || data.type === "event") {
                    if (!floatCurrentStatus) floatCurrentStatus = appendMini(data.message || "处理中…", "status");
                    else floatCurrentStatus.textContent = data.message || "处理中…";
                } else if (data.type === "token") {
                    ensureFloatStreamingAgent().textContent += data.content;
                    floatingChatMessages.scrollTop = floatingChatMessages.scrollHeight;
                } else if (data.type === "result") {
                    if (floatCurrentStatus) { floatCurrentStatus.remove(); floatCurrentStatus = null; }
                    let html = escapeHtml(data.reply).replace(/\\n/g, "<br>");
                    (data.files || []).forEach(f => {
                        const href = f.startsWith("/") ? f : "/" + f;
                        html += `<a class="file-link" href="${href}" target="_blank">📎 ${f.split("/").pop()}</a>`;
                    });
                    if (floatStreamingMsg) {
                        floatStreamingMsg.innerHTML = html;
                        floatStreamingMsg = null;
                    } else {
                        appendMini(html, "agent");
                    }
                    finishFloatTurn();
                    if (!floatingChatEl.classList.contains("open")) {
                        floatUnread += 1;
                        updateFloatBadge();
                    }
                } else if (data.type === "error") {
                    if (floatCurrentStatus) { floatCurrentStatus.remove(); floatCurrentStatus = null; }
                    if (floatStreamingMsg) { floatStreamingMsg.remove(); floatStreamingMsg = null; }
                    appendMini(data.message, "error");
                    finishFloatTurn();
                }
            };
        }

        function sendFloating() {
            const text = floatingChatInput.value.trim();
            if (!text) return;
            if (!wsFloat || wsFloat.readyState !== WebSocket.OPEN) {
                appendMini("尚未连接到服务器", "error");
                return;
            }
            appendMini(escapeHtml(text).replace(/\\n/g, "<br>"), "user");
            floatingChatInput.value = "";
            floatingChatInput.disabled = true;
            floatingChatSend.disabled = true;
            floatStreamingMsg = null;
            floatCurrentStatus = appendMini("销销正在思考…", "status");
            wsFloat.send(JSON.stringify({ user_id: userSelect.value, message: text }));
        }

        floatingChatSend.addEventListener("click", sendFloating);
        floatingChatInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendFloating(); });

        floatingChatToggle.addEventListener("click", () => {
            floatingChatEl.classList.add("open");
            floatingChatToggle.classList.remove("open");
            floatUnread = 0;
            updateFloatBadge();
            floatingChatInput.focus();
        });

        floatingChatClose.addEventListener("click", () => {
            floatingChatEl.classList.remove("open");
            const currentPanel = document.querySelector(".panel.active").id.replace("panel-", "");
            if (currentPanel !== "collab") {
                floatingChatToggle.classList.add("open");
            }
        });

        loadUsers().then(() => {
            pollTask();
            loadTaskHistory();
        });
        connect();
        setInterval(pollTask, 3000);
        connectFloatingChat();
    </script>
</body>
</html>'''


@app.get("/chat", response_class=HTMLResponse)
def chat_page():
    """Web 聊天页面"""
    return CHAT_HTML


@app.get("/wechat_kb", response_class=HTMLResponse)
def wechat_kb_page():
    """行业资讯库页面：直接渲染 wechat-digest-skill 生成的 digest.html"""
    if not WECHAT_KB_DIGEST_PATH.exists():
        return HTMLResponse(
            """<h1>digest.html 不存在</h1>
            <p>请先在 wechat-digest-skill 目录运行：</p>
            <pre>python3 kb.py export-html</pre>
            <p>期望路径：{}</p>""".format(WECHAT_KB_DIGEST_PATH),
            status_code=404
        )
    return HTMLResponse(WECHAT_KB_DIGEST_PATH.read_text(encoding="utf-8"))

@app.get("/api/wechat_kb/stats")
def wechat_kb_stats():
    return wechat_digest.get_stats()


@app.post("/api/wechat_kb/sync")
async def wechat_kb_sync(kb_path: str = ""):
    path = kb_path or None
    result = await run_in_threadpool(wechat_digest.sync, path)
    return result


@app.get("/api/wechat_kb/search")
def wechat_kb_search(q: str = "", top_k: int = 10):
    if not q.strip():
        return {"results": []}
    return {"results": wechat_digest.search(q, top_k=top_k)}


@app.get("/api/wechat_kb/articles")
def wechat_kb_articles(limit: int = 50, offset: int = 0, account: str = "", tag: str = ""):
    return {
        "articles": wechat_digest.list_articles(
            limit=limit, offset=offset, account=account, tag=tag
        )
    }


@app.get("/api/wechat_kb/leads")
def wechat_kb_leads(q: str = "", industry: str = "", signal: str = "", service: str = "", limit: int = 200):
    """返回资讯工作台提取的销售线索，支持按行业/信号/服务机会/关键词过滤。"""
    kb = _load_kb_json()
    leads = []
    for ls in kb.get("leads", {}).values():
        leads.extend(ls or [])
    leads.sort(key=lambda x: x.get("publishDate") or "", reverse=True)

    q = q.strip().lower()
    industry = industry.strip()
    signal = signal.strip()
    service = service.strip()

    def match(l):
        if industry and l.get("industry") != industry:
            return False
        if signal and l.get("signal") != signal:
            return False
        if service and service not in (l.get("serviceOpportunities") or []):
            return False
        if q:
            text = " ".join([
                l.get("company", ""),
                l.get("industry", ""),
                l.get("signal", ""),
                l.get("playbook", ""),
                " ".join(l.get("relatedCompanies") or []),
                " ".join(l.get("similarIndustries") or []),
                " ".join(l.get("serviceOpportunities") or []),
            ]).lower()
            if q not in text:
                return False
        return True

    filtered = [l for l in leads if match(l)]
    return {"leads": filtered[:limit], "total": len(filtered)}


@app.get("/api/wechat_kb/leads/{lead_id}")
def wechat_kb_lead_detail(lead_id: str):
    """按 id 返回单条线索详情。"""
    kb = _load_kb_json()
    for ls in kb.get("leads", {}).values():
        for l in ls:
            if l.get("id") == lead_id:
                return l
    return JSONResponse({"error": "线索不存在"}, status_code=404)


@app.get("/api/wechat_kb/company_leads")
def wechat_kb_company_leads(company: str = "", limit: int = 200):
    """以客户视角聚合线索：从知识库线索中抽取每个公司，返回竞品池/可扩展服务/相似行业。

    不需要 CRM，只基于已提取的线索做统计推导。
    """
    kb = _load_kb_json()
    all_leads = []
    for ls in kb.get("leads", {}).values():
        all_leads.extend(ls or [])

    def _norm(s):
        return str(s or "").strip().lower()

    # 收集所有出现过的公司（线索主体 + 竞品池）
    company_names = sorted(set(_norm(l.get("company")) for l in all_leads if l.get("company")))
    if company:
        cq = _norm(company)
        company_names = [c for c in company_names if cq in c or c in cq]

    def _leads_about(name):
        return [l for l in all_leads if _norm(l.get("company")) == name or name in [_norm(x) for x in l.get("relatedCompanies") or []]]

    result = []
    for c in company_names:
        matched = _leads_about(c)
        # 优先用「以该公司为主体」的线索定行业
        direct = [l for l in matched if _norm(l.get("company")) == c]
        industry = ""
        if direct:
            industry = max((l.get("industry") for l in direct if l.get("industry")), key=lambda x: sum(1 for ll in direct if ll.get("industry") == x), default="")
        else:
            industry = max((l.get("industry") for l in matched if l.get("industry")), key=lambda x: sum(1 for ll in matched if ll.get("industry") == x), default="")

        competitors = sorted(set(
            x for l in matched for x in (l.get("relatedCompanies") or [])
            if _norm(x) != c
        ))
        similar_inds = sorted(set(
            x for l in matched for x in (l.get("similarIndustries") or [])
        ))
        services = sorted(set(
            x for l in matched for x in (l.get("serviceOpportunities") or [])
        ))

        # 服务扩展：同行业其他公司做、但这家公司当前线索里没出现的服务
        expandable = []
        if industry:
            industry_services = set(
                _norm(x) for l in all_leads
                if _norm(l.get("industry")) == _norm(industry) and l not in matched
                for x in (l.get("serviceOpportunities") or [])
            )
            own_services = set(_norm(x) for x in services)
            expandable = sorted([x for x in set(
                x for l in all_leads
                if _norm(l.get("industry")) == _norm(industry) and l not in matched
                for x in (l.get("serviceOpportunities") or [])
            ) if _norm(x) not in own_services])

        latest = ""
        if matched:
            latest = max((l.get("publishDate") or "") for l in matched)

        result.append({
            "name": (direct[0] if direct else matched[0]).get("company") if matched else c,
            "industry": industry,
            "matched_lead_ids": [l.get("id") for l in matched],
            "matched_leads_count": len(matched),
            "competitors": competitors,
            "similar_industries": similar_inds,
            "service_opportunities": services,
            "expandable_services": expandable,
            "latest_lead_date": latest,
        })

    # 排序：匹配线索数多的优先，再按最新线索时间倒序，最后按名称
    result.sort(key=lambda x: x["name"])
    result.sort(key=lambda x: x["latest_lead_date"] or "", reverse=True)
    result.sort(key=lambda x: x["matched_leads_count"], reverse=True)
    return {"companies": result[:limit], "total": len(result)}


@app.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    """Web 聊天 WebSocket：接收消息，流式返回进度和最终结果"""
    await ws.accept()
    loop = asyncio.get_event_loop()
    disconnected = False

    async def safe_send_json(data: dict):
        nonlocal disconnected
        if disconnected or ws.client_state == WebSocketState.DISCONNECTED:
            return
        try:
            await ws.send_json(data)
        except Exception:
            disconnected = True

    def event_sink(event: AgentEvent):
        if disconnected:
            return
        if event.type == AgentEventType.TOKEN:
            payload = {
                "type": "token",
                "content": event.message or "",
            }
        else:
            payload = {
                "type": "event",
                "event_type": event.type.value if hasattr(event.type, "value") else str(event.type),
                "message": event.message or "",
                "step": event.step,
                "total": event.total,
            }
        asyncio.run_coroutine_threadsafe(safe_send_json(payload), loop)

    try:
        while True:
            data = await ws.receive_json()
            user_id = data.get("user_id", "web_user").strip() or "web_user"
            action = data.get("action", "").strip()

            if action == "stop":
                event = active_cancel_events.get(user_id)
                if event:
                    event.set()
                    await safe_send_json({"type": "status", "message": "已停止生成"})
                else:
                    await safe_send_json({"type": "status", "message": "当前没有正在运行的任务"})
                continue

            message = data.get("message", "").strip()
            if not message:
                await safe_send_json({"type": "error", "message": "消息不能为空"})
                continue

            # 为当前用户创建取消信号
            cancel_event = threading.Event()
            active_cancel_events[user_id] = cancel_event

            await safe_send_json({"type": "status", "message": "销销正在思考…"})

            try:
                result = await run_in_threadpool(
                    process_query, user_id, message,
                    event_sink=event_sink, cancel_event=cancel_event
                )

                await safe_send_json({
                    "type": "result",
                    "reply": result.get("reply", ""),
                    "files": result.get("files", []),
                    "found_files": result.get("found_files", []),
                })
            finally:
                active_cancel_events.pop(user_id, None)
    except WebSocketDisconnect:
        disconnected = True
        logger.info("Web 聊天连接断开")
    except Exception as e:
        disconnected = True
        logger.error(f"Web 聊天异常: {e}", exc_info=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
