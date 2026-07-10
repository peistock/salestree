"""
Agent 执行公共逻辑
- 与渠道无关的 Agent 调用封装
- CLI 和 Web 服务共用同一套入口
"""
import os
import threading
from pathlib import Path

from mind.agent import FamilyAgent


# ========== 用户映射 ==========
NAME_MAP = {
    "grandpa": "爷爷", "grandma": "奶奶",
    "grandpa2": "外公", "grandma2": "外婆",
    "dad": "爸爸", "mom": "妈妈"
}


def get_user_name(user_id: str) -> str:
    """user_id → 显示名称"""
    return NAME_MAP.get(user_id, "同事")


# ========== 文件请求处理 ==========
_FILE_KEYWORDS = ["发给我", "给我文件", "给我pdf", "给我报告", "文件发", "pdf发", "文件", "pdf", "报告发", "发过来"]
_FILE_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".mp3", ".mp4", ".docx", ".pptx")


def is_file_request(query: str) -> bool:
    """检测是否为文件索取请求"""
    return any(k in query.lower() for k in _FILE_KEYWORDS)


def scan_user_files(user_id: str, max_files: int = 3) -> list[Path]:
    """扫描用户最近任务目录中的文件"""
    data_dir = Path(os.getenv("DATA_DIR", "./data")) / "tasks"
    if not data_dir.exists():
        return []

    task_dirs = sorted(
        [d for d in data_dir.iterdir() if d.is_dir() and user_id in d.name],
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )[:3]

    found = []
    for td in task_dirs:
        for p in td.rglob("*"):
            if p.is_file() and p.stat().st_size > 100 and p.suffix.lower() in _FILE_SUFFIXES:
                found.append(p)
    return found[:max_files]


# ========== Agent 统一入口 ==========
def run_agent(user_id: str, query: str, event_sink=None, cancel_event: threading.Event | None = None) -> dict:
    """
    统一 Agent 执行入口
    返回: {"reply": str, "mode": int, "files": list[str]}
    """
    user_name = get_user_name(user_id)
    agent = FamilyAgent(user_id, user_name)
    return agent.run(query, event_sink=event_sink, cancel_event=cancel_event)


# ========== 查询处理核心 ==========
def process_query(user_id: str, query: str, event_sink=None, cancel_event: threading.Event | None = None) -> dict:
    """
    处理用户查询的核心逻辑（与渠道无关）
    返回结构化结果，由调用方决定如何展示/推送
    """
    # 1. 文件请求快速路由
    if is_file_request(query):
        found = scan_user_files(user_id)
        if found:
            return {
                "reply": "找到啦，销销发给您～",
                "mode": 1,
                "files": [],
                "found_files": [str(f) for f in found],
                "is_file_request": True,
            }
        else:
            return {
                "reply": "销销没找到之前的文件呢，您是要我重新做一份吗？",
                "mode": 1,
                "files": [],
                "found_files": [],
                "is_file_request": True,
            }

    # 2. Agent 执行
    result = run_agent(user_id, query, event_sink=event_sink, cancel_event=cancel_event)
    return {
        "reply": result.get("reply", ""),
        "mode": result.get("mode", 1),
        "files": result.get("files", []),
        "found_files": [],
        "is_file_request": False,
    }
