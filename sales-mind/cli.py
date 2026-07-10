"""
销销 CLI 模式
- 终端直接对话，无需启动 Web 服务
- 复用 FamilyAgent 全部能力（记忆、技能、工具调用）
- 与 Web 服务共用 agent_runner 核心逻辑

用法:
    python cli.py              # 交互模式
    python cli.py "查一下天气"  # 单次命令模式
"""
import os
import sys
import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# 代理配置（同 main.py）
for _proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    _proxy_val = os.getenv(_proxy_key)
    if _proxy_val:
        os.environ[_proxy_key] = _proxy_val

from mind.memory import init_db
from mind.agent_events import AgentEvent, AgentEventType
from mind.agent_runner import process_query, get_user_name

# ========== 日志配置 ==========
LOG_DIR = Path(os.getenv("DATA_DIR", "./data")).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_file_handler = logging.FileHandler(LOG_DIR / "cli.log", encoding="utf-8")
_file_handler.setLevel(logging.INFO)
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.WARNING)

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[_file_handler, _console_handler],
)
logger = logging.getLogger(__name__)

# ========== CLI 用户标识 ==========
CLI_USER_ID = os.getenv("CLI_USER_ID", "cli")
CLI_USER_NAME = os.getenv("CLI_USER_NAME", "开发者")


def _event_sink(event: AgentEvent):
    """CLI 进度回调：打印到终端"""
    if event.type == AgentEventType.AGENT_START and event.resumed:
        print("[销销] 接着刚才的继续干～")
    elif event.type == AgentEventType.HEARTBEAT:
        print(f"[销销] {event.message or '还在处理中...'}")


def _display_result(result: dict):
    """在终端展示处理结果"""
    reply = result.get("reply", "")
    files = result.get("files", [])
    found_files = result.get("found_files", [])

    # 文件请求：展示找到的文件
    if result.get("is_file_request"):
        if found_files:
            print(reply)
            for fpath in found_files:
                print(f"  📎 {fpath}")
        else:
            print(reply)
        return

    # 普通回复
    print(reply)

    # 生成文件
    if files:
        print(f"\n[生成文件]")
        for fpath in files:
            print(f"  📎 {fpath}")


def run_once(user_id: str, query: str) -> dict:
    """执行单次对话，返回结构化结果"""
    return process_query(user_id, query, event_sink=_event_sink)


def interactive_mode():
    """交互式对话循环"""
    print("=" * 50)
    print("销销 CLI 模式")
    print(f"用户: {CLI_USER_NAME} ({CLI_USER_ID})")
    print("输入 /exit 或 /quit 退出，/new 开启新会话")
    print("=" * 50)

    init_db()

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见～")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/exit", "/quit", "/q"):
            print("再见～")
            break

        if user_input.lower() == "/new":
            print("[新会话已开启]")
            continue

        print("\n销销: ", end="", flush=True)
        result = run_once(CLI_USER_ID, user_input)
        _display_result(result)


def single_mode(query: str):
    """单次命令模式"""
    init_db()
    result = run_once(CLI_USER_ID, query)
    _display_result(result)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        single_mode(" ".join(sys.argv[1:]))
    else:
        interactive_mode()
