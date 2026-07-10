"""
DuckDB 分析层 —— Agent 自我观察与资产目录

设计原则：
- 不替代 PG，而是作为分析副库
- 直接读取文件轨迹，无需 ETL
- 可 attach PG 做联合查询
"""

import duckdb
import json
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class AnalyticsStore:
    """DuckDB 分析存储，供 Agent 查询自身历史与资产"""

    def __init__(self, db_path: str = None):
        root = Path(__file__).resolve().parent.parent
        self.db_path = str(root / (db_path or "data/analytics.duckdb"))
        self.conn = duckdb.connect(self.db_path)
        self._init_tables()
        self._attach_pg()
        logger.info(f"AnalyticsStore 初始化完成: {self.db_path}")

    def _init_tables(self):
        """创建核心分析表"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_turns (
                trace_id VARCHAR,
                user_id VARCHAR,
                timestamp TIMESTAMP,
                iteration INTEGER,
                tool VARCHAR,
                args_json JSON,
                result_preview VARCHAR,
                latency_ms DOUBLE,
                source_file VARCHAR
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS task_assets (
                task_id VARCHAR PRIMARY KEY,
                user_id VARCHAR,
                created_at TIMESTAMP,
                asset_type VARCHAR,
                md_path VARCHAR,
                pdf_path VARCHAR,
                other_files JSON,
                file_count INTEGER,
                has_checkpoint BOOLEAN
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_summary (
                trace_id VARCHAR PRIMARY KEY,
                user_id VARCHAR,
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
                duration_sec DOUBLE,
                turn_count INTEGER,
                unique_tools VARCHAR[],
                failed_tools INTEGER,
                final_status VARCHAR
            )
        """)

        self.conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_learnings START 1")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_learnings (
                id INTEGER DEFAULT nextval('seq_learnings') PRIMARY KEY,
                category VARCHAR,
                content TEXT,
                source_session VARCHAR,
                confidence FLOAT,
                created_at TIMESTAMP,
                last_applied TIMESTAMP,
                apply_count INTEGER DEFAULT 0
            )
        """)

        # 创建常用索引
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_user ON agent_turns(user_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_tool ON agent_turns(tool)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_time ON agent_turns(timestamp)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_user ON task_assets(user_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_learnings_cat ON agent_learnings(category)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_learnings_conf ON agent_learnings(confidence)")

    def _attach_pg(self):
        """Attach PG 做联合查询"""
        try:
            pg_host = os.getenv("DB_HOST", "localhost")
            pg_port = os.getenv("DB_PORT", "5432")
            pg_user = os.getenv("DB_USER", "family")
            pg_pass = os.getenv("DB_PASSWORD", "salesmind2026")
            pg_db = os.getenv("DB_NAME", "salesmind")

            pg_dsn = f"host={pg_host} port={pg_port} user={pg_user} password={pg_pass} dbname={pg_db}"

            # 检查是否已 attach
            attached = self.conn.execute("""
                SELECT database_name FROM duckdb_databases()
                WHERE database_name = 'pg'
            """).fetchall()
            if attached:
                return

            # DuckDB attach postgres
            self.conn.execute(f"""
                ATTACH '{pg_dsn}' AS pg (TYPE POSTGRES)
            """)
            logger.info("PG 已 attach 到 DuckDB")
        except Exception as e:
            logger.warning(f"Attach PG 失败（不影响核心功能）: {e}")

    def ingest_traces(self, pattern: str = None) -> str:
        """
        从 JSONL 轨迹文件加载数据
        返回统计信息
        """
        root = Path(__file__).resolve().parent.parent
        if pattern is None:
            pattern = str(root / "data" / "traces" / "**" / "*.jsonl")

        # 使用 DuckDB 的 read_json_auto 直接读取
        try:
            # 先清空旧数据（全量刷新）
            self.conn.execute("DELETE FROM agent_turns")

            # 读取所有 JSONL
            self.conn.execute(f"""
                INSERT INTO agent_turns
                SELECT
                    trace_id,
                    user_id,
                    try_cast(timestamp AS TIMESTAMP) as timestamp,
                    iteration,
                    tool,
                    args as args_json,
                    result_preview,
                    latency_ms,
                    filename as source_file
                FROM read_json_auto('{pattern}', format='newline_delimited')
            """)

            count = self.conn.execute("SELECT COUNT(*) FROM agent_turns").fetchone()[0]
            return f"轨迹数据已加载: {count} 条记录"
        except Exception as e:
            logger.error(f"加载轨迹失败: {e}")
            # 降级：逐文件读取
            return self._ingest_traces_fallback(root / "data" / "traces")

    def _ingest_traces_fallback(self, traces_dir: Path) -> str:
        """降级方案：逐文件解析 JSONL"""
        total = 0
        for jsonl_file in traces_dir.rglob("*.jsonl"):
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        self.conn.execute("""
                            INSERT INTO agent_turns
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, [
                            record.get("trace_id"),
                            record.get("user_id"),
                            record.get("timestamp"),
                            record.get("iteration"),
                            record.get("tool"),
                            json.dumps(record.get("args", {})),
                            str(record.get("result_preview", ""))[:500],
                            record.get("latency_ms", 0),
                            str(jsonl_file)
                        ])
                        total += 1
                    except Exception:
                        continue

        return f"轨迹数据已加载（降级模式）: {total} 条记录"

    def ingest_tasks(self, tasks_dir: str = None) -> str:
        """
        扫描任务目录，建立资产目录
        """
        root = Path(__file__).resolve().parent.parent
        if tasks_dir is None:
            tasks_dir = root / "data" / "tasks"
        else:
            tasks_dir = Path(tasks_dir)

        self.conn.execute("DELETE FROM task_assets")

        count = 0
        for task_dir in tasks_dir.iterdir():
            if not task_dir.is_dir():
                continue
            if task_dir.name.startswith("."):
                continue

            task_id = task_dir.name
            # 解析 user_id: ChenPei_xxx → ChenPei, x-xxx → None
            if "_" in task_id and not task_id.startswith("x-"):
                user_id = task_id.split("_")[0]
            else:
                user_id = None

            # 扫描文件
            md_path = None
            pdf_path = None
            other_files = []
            has_checkpoint = False
            created_at = datetime.fromtimestamp(task_dir.stat().st_ctime)

            for f in task_dir.rglob("*"):
                if f.is_dir():
                    continue
                rel = str(f.relative_to(task_dir))
                if f.suffix == ".md":
                    md_path = rel
                elif f.suffix == ".pdf":
                    pdf_path = rel
                elif f.name == "checkpoint.json":
                    has_checkpoint = True
                elif f.suffix in (".py", ".json", ".txt", ".css", ".html"):
                    other_files.append(rel)

            # 去重：处理双重嵌套路径
            if md_path:
                md_path = self._dedup_nested_path(md_path)
            if pdf_path:
                pdf_path = self._dedup_nested_path(pdf_path)
            other_files = [self._dedup_nested_path(p) for p in other_files]

            # 判断资产类型
            asset_type = self._detect_asset_type(task_dir, md_path)

            self.conn.execute("""
                INSERT OR REPLACE INTO task_assets
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                task_id, user_id, created_at, asset_type,
                md_path, pdf_path, json.dumps(other_files),
                len([md_path, pdf_path] + other_files),
                has_checkpoint
            ])
            count += 1

        return f"任务资产已扫描: {count} 个任务目录"

    def _dedup_nested_path(self, path: str) -> str:
        """去除重复嵌套路径，如 data/tasks/x/x/file.md → file.md"""
        parts = Path(path).parts
        # 检测 data/tasks/<id>/data/tasks/<id>/ 模式
        if len(parts) >= 6 and parts[0] == "data" and parts[1] == "tasks":
            # 找到第二个 data/tasks 出现的位置
            for i in range(2, len(parts) - 2):
                if parts[i] == "data" and parts[i+1] == "tasks":
                    return str(Path(*parts[i+2:]))
        return path

    def _detect_asset_type(self, task_dir: Path, md_path: Optional[str]) -> str:
        """根据内容检测资产类型"""
        if not md_path:
            return "unknown"

        md_file = task_dir / md_path
        if not md_file.exists():
            md_file = task_dir / md_path.replace("data/tasks/", "").replace("/", "_")
            if not md_file.exists():
                return "unknown"

        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")[:2000]
            if "深度分析" in content or "研究报告" in content:
                return "deep_analysis"
            elif "横纵分析" in content:
                return "hv_analysis"
            elif "# " in content and len(content) > 5000:
                return "report"
            else:
                return "note"
        except Exception:
            return "unknown"

    def build_execution_summary(self) -> str:
        """从 agent_turns 生成执行摘要"""
        self.conn.execute("DELETE FROM execution_summary")

        self.conn.execute("""
            INSERT INTO execution_summary
            SELECT
                trace_id,
                user_id,
                MIN(timestamp) as started_at,
                MAX(timestamp) as ended_at,
                EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) as duration_sec,
                COUNT(*) as turn_count,
                LIST_DISTINCT(LIST(tool)) as unique_tools,
                COUNT(*) FILTER (WHERE result_preview LIKE '%失败%' OR result_preview LIKE '%错误%' OR result_preview LIKE '%error%' OR result_preview LIKE '%404%' OR result_preview LIKE '%500%') as failed_tools,
                CASE
                    WHEN COUNT(*) FILTER (WHERE tool = 'md_to_pdf') > 0 THEN 'pdf_generated'
                    WHEN COUNT(*) FILTER (WHERE tool = 'write_file') > 0 THEN 'file_written'
                    ELSE 'unknown'
                END as final_status
            FROM agent_turns
            GROUP BY trace_id, user_id
        """)

        count = self.conn.execute("SELECT COUNT(*) FROM execution_summary").fetchone()[0]
        return f"执行摘要已生成: {count} 条记录"

    def query(self, sql: str) -> List[Dict[str, Any]]:
        """执行分析查询"""
        try:
            result = self.conn.execute(sql).fetchdf()
            return result.to_dict("records")
        except Exception as e:
            return [{"error": str(e)}]

    def get_user_history(self, user_id: str, limit: int = 20) -> List[Dict]:
        """获取用户最近的执行摘要"""
        return self.query(f"""
            SELECT
                trace_id,
                started_at,
                duration_sec,
                turn_count,
                unique_tools,
                final_status
            FROM execution_summary
            WHERE user_id = '{user_id}'
            ORDER BY started_at DESC
            LIMIT {limit}
        """)

    def get_task_assets(self, user_id: Optional[str] = None) -> List[Dict]:
        """获取任务资产列表"""
        where_clause = f"WHERE user_id = '{user_id}'" if user_id else ""
        return self.query(f"""
            SELECT
                task_id,
                user_id,
                created_at,
                asset_type,
                md_path,
                pdf_path,
                file_count,
                has_checkpoint
            FROM task_assets
            {where_clause}
            ORDER BY created_at DESC
        """)

    def search_past_executions(self, query_text: str, user_id: Optional[str] = None) -> List[Dict]:
        """搜索历史执行记录（基于工具名和结果预览）"""
        where_user = f"AND user_id = '{user_id}'" if user_id else ""
        return self.query(f"""
            SELECT
                trace_id,
                user_id,
                timestamp,
                tool,
                result_preview
            FROM agent_turns
            WHERE (tool ILIKE '%{query_text}%'
                   OR result_preview ILIKE '%{query_text}%')
                {where_user}
            ORDER BY timestamp DESC
            LIMIT 20
        """)

    def get_tool_stats(self, days: int = 7) -> List[Dict]:
        """获取最近 N 天的工具使用统计"""
        return self.query(f"""
            SELECT
                tool,
                COUNT(*) as call_count,
                COUNT(DISTINCT trace_id) as task_count,
                AVG(latency_ms) as avg_latency_ms,
                COUNT(*) FILTER (WHERE result_preview LIKE '%失败%'
                                 OR result_preview LIKE '%错误%'
                                 OR result_preview LIKE '%error%') as fail_count
            FROM agent_turns
            WHERE timestamp > CURRENT_TIMESTAMP - INTERVAL '{days} days'
            GROUP BY tool
            ORDER BY call_count DESC
        """)

    def get_asset_overview(self) -> Dict[str, Any]:
        """资产概览统计"""
        total = self.query("SELECT COUNT(*) as total FROM task_assets")[0]
        by_type = self.query("""
            SELECT asset_type, COUNT(*) as count
            FROM task_assets
            GROUP BY asset_type
            ORDER BY count DESC
        """)
        by_user = self.query("""
            SELECT user_id, COUNT(*) as count
            FROM task_assets
            WHERE user_id IS NOT NULL
            GROUP BY user_id
            ORDER BY count DESC
        """)
        pdf_count = self.query("""
            SELECT COUNT(*) as count FROM task_assets WHERE pdf_path IS NOT NULL
        """)[0]

        return {
            "total_tasks": total.get("total", 0),
            "pdf_count": pdf_count.get("count", 0),
            "by_type": by_type,
            "by_user": by_user
        }

    # ========== agent_learnings 方法 ==========

    def extract_learnings(self) -> str:
        """
        从 agent_turns 自动提取学习规则：
        - 失败模式（工具调用失败、搜索无结果）
        - 耗时异常（单次工具调用 > 10s）
        - 高频工具组合（哪些工具经常一起用）
        """
        inserted = 0

        # 1. 提取失败模式
        failures = self.conn.execute("""
            SELECT
                trace_id,
                tool,
                result_preview,
                timestamp
            FROM agent_turns
            WHERE result_preview LIKE '%失败%'
               OR result_preview LIKE '%错误%'
               OR result_preview LIKE '%404%'
               OR result_preview LIKE '%500%'
               OR result_preview LIKE '%Unable to connect%'
               OR result_preview LIKE '%timeout%'
            ORDER BY timestamp DESC
            LIMIT 50
        """).fetchall()

        for row in failures:
            trace_id, tool, preview, ts = row
            # 去重：同一 trace + 同一 tool 只记一次
            exists = self.conn.execute("""
                SELECT COUNT(*) FROM agent_learnings
                WHERE source_session = ? AND content LIKE ?
            """, [trace_id, f"%{tool}%"]).fetchone()[0]
            if exists:
                continue

            content = f"工具 [{tool}] 调用失败: {preview[:200]}"
            self.conn.execute("""
                INSERT INTO agent_learnings (category, content, source_session, confidence, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, ["failure", content, trace_id, 0.7, ts])
            inserted += 1

        # 2. 提取耗时异常（单次 > 15s）
        slow = self.conn.execute("""
            SELECT trace_id, tool, latency_ms, timestamp
            FROM agent_turns
            WHERE latency_ms > 15000
            ORDER BY timestamp DESC
            LIMIT 30
        """).fetchall()

        for row in slow:
            trace_id, tool, latency, ts = row
            exists = self.conn.execute("""
                SELECT COUNT(*) FROM agent_learnings
                WHERE source_session = ? AND content LIKE ?
            """, [trace_id, f"%耗时%{tool}%"]).fetchone()[0]
            if exists:
                continue

            content = f"工具 [{tool}] 响应过慢 ({latency/1000:.1f}s)，考虑：1)增加超时 2)换用备用源 3)缓存结果"
            self.conn.execute("""
                INSERT INTO agent_learnings (category, content, source_session, confidence, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, ["rule", content, trace_id, 0.6, ts])
            inserted += 1

        # 3. 提取高频成功模式（哪些工具组合能产出 PDF）
        success_patterns = self.conn.execute("""
            SELECT
                trace_id,
                LIST(DISTINCT tool ORDER BY tool) as tools,
                COUNT(*) as turn_count
            FROM agent_turns
            WHERE trace_id IN (
                SELECT trace_id FROM agent_turns WHERE tool = 'md_to_pdf'
            )
            GROUP BY trace_id
            HAVING COUNT(*) > 5
            ORDER BY COUNT(*) DESC
            LIMIT 10
        """).fetchall()

        for row in success_patterns:
            trace_id, tools, count = row
            tools_str = str(tools) if tools else ""
            exists = self.conn.execute("""
                SELECT COUNT(*) FROM agent_learnings
                WHERE category = 'success' AND source_session = ?
            """, [trace_id]).fetchone()[0]
            if exists:
                continue

            content = f"成功生成报告的典型工具链: {tools_str}（共 {count} 轮）"
            self.conn.execute("""
                INSERT INTO agent_learnings (category, content, source_session, confidence, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, ["success", content, trace_id, 0.5])
            inserted += 1

        return f"自动提取学习记录: {inserted} 条"

    def save_learning(self, category: str, content: str, source_session: str = None, confidence: float = 0.8) -> str:
        """手动保存一条学习记录"""
        self.conn.execute("""
            INSERT INTO agent_learnings (category, content, source_session, confidence, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [category, content, source_session, confidence])
        return f"学习记录已保存: [{category}] {content[:50]}..."

    def get_learnings(self, category: str = None, limit: int = 20) -> List[Dict]:
        """获取学习记录"""
        where = f"WHERE category = '{category}'" if category else ""
        return self.query(f"""
            SELECT id, category, content, source_session, confidence, created_at, apply_count
            FROM agent_learnings
            {where}
            ORDER BY confidence DESC, created_at DESC
            LIMIT {limit}
        """)

    def increment_apply_count(self, learning_id: int) -> None:
        """增加学习记录的应用次数"""
        self.conn.execute("""
            UPDATE agent_learnings
            SET apply_count = apply_count + 1, last_applied = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [learning_id])

    def get_relevant_learnings(self, query_text: str, limit: int = 5) -> List[Dict]:
        """根据查询文本获取相关的学习记录（简单关键词匹配）"""
        return self.query(f"""
            SELECT id, category, content, confidence, apply_count
            FROM agent_learnings
            WHERE content ILIKE '%{query_text}%'
               OR category ILIKE '%{query_text}%'
            ORDER BY confidence DESC, apply_count DESC
            LIMIT {limit}
        """)

    def refresh(self) -> str:
        """全量刷新所有数据"""
        results = []
        results.append(self.ingest_traces())
        results.append(self.ingest_tasks())
        results.append(self.build_execution_summary())
        results.append(self.extract_learnings())
        return "\n".join(results)

    def close(self):
        self.conn.close()


# 全局单例
_store: Optional[AnalyticsStore] = None


def get_store() -> AnalyticsStore:
    global _store
    if _store is None:
        _store = AnalyticsStore()
    return _store
