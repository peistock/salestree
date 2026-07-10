"""
Agent 检索轨迹持久化（OpenViking 启发 #4）
- 记录每次 Agent 运行的工具调用链路
- 供外挂大脑（Streamlit）查看和诊断
- 存储格式：JSONL，按用户+日期分文件
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))


class AgentTraceStore:
    """Agent 执行轨迹存储（JSONL 格式）"""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.trace_id = f"{user_id}_{int(time.time())}"
        self.start_time = time.time()

    def log_turn(self, iteration: int, tool_name: str, args: dict,
                 result_preview: str, latency_ms: float = 0):
        """记录一次工具调用"""
        entry = {
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "iteration": iteration,
            "tool": tool_name,
            "args": {k: str(v)[:200] for k, v in args.items() if k != "api_key"},
            "result_preview": result_preview[:500] if result_preview else "",
            "latency_ms": round(latency_ms, 1),
        }
        self._flush_entry(entry)

    def _flush_entry(self, entry: dict):
        """追加写入 JSONL"""
        try:
            trace_dir = DATA_DIR / "traces" / self.user_id
            trace_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            fpath = trace_dir / f"{date_str}.jsonl"
            with open(fpath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"轨迹写入失败: {e}")

    @staticmethod
    def query(user_id: str, date_str: Optional[str] = None,
              limit: int = 100) -> List[Dict]:
        """查询用户的最近轨迹"""
        trace_dir = DATA_DIR / "traces" / user_id
        if not trace_dir.exists():
            return []

        if date_str is None:
            files = sorted(trace_dir.glob("*.jsonl"), reverse=True)
            if not files:
                return []
            fpath = files[0]
        else:
            fpath = trace_dir / f"{date_str}.jsonl"
            if not fpath.exists():
                return []

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                lines = f.readlines()
            traces = [json.loads(l) for l in lines if l.strip()]
            return traces[-limit:]
        except Exception as e:
            logger.warning(f"轨迹查询失败: {e}")
            return []

    @staticmethod
    def get_latest_run_summary(user_id: str) -> str:
        """获取用户最近一次 Agent 运行的摘要"""
        traces = AgentTraceStore.query(user_id, limit=50)
        if not traces:
            return "（无最近运行记录）"

        runs: Dict[str, List] = {}
        for t in traces:
            tid = t.get("trace_id", "unknown")
            runs.setdefault(tid, []).append(t)

        latest_tid = max(runs.keys())
        latest = runs[latest_tid]

        lines = [f"运行 ID: {latest_tid}", f"工具调用数: {len(latest)}"]
        for t in latest:
            tool = t["tool"]
            preview = t["result_preview"][:60] if t.get("result_preview") else ""
            lines.append(f"  [{t['iteration']}] {tool} — {preview}...")
        return "\n".join(lines)
