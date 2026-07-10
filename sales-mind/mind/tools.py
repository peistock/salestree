"""
工具集（Agent 可调用的工具）
【安全注意】已删除 run_python 代码执行工具
Phase 2 升级：search_knowledge 接入向量检索
v1.1 升级：引入 @tool 装饰器 + ToolResult 标准信封
"""
import os
import json
import urllib.request
import urllib.error
import subprocess
import shlex
import threading
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import logging

from psycopg2.extras import RealDictCursor
from mind.tool_result import tool, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

WORK_DIR = Path(os.getenv("DATA_DIR", "./data"))
KNOWLEDGE_DIR = WORK_DIR / "knowledge"
KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

# 后台 PDF 生成任务状态（path -> dict）
_pdf_jobs: Dict[str, dict] = {}
_pdf_jobs_lock = threading.Lock()


def _searxng_search_url() -> str:
    """从环境变量读取 SearXNG 地址，默认本地 127.0.0.1:8080"""
    base = os.getenv("SEARXNG_URL", "http://127.0.0.1:8080").rstrip("/")
    return base + "/search"


class Toolkit:
    """Agent 可调用的安全工具（v1.1 标准信封版）"""

    def __init__(self, work_dir: Path = None):
        self.work_dir = work_dir or WORK_DIR
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._kb = None  # 懒加载知识库
        self._browser = None  # 懒加载浏览器
        self._registry = ToolRegistry()
        self._registry.scan_instance(self)
        # Redis 缓存客户端（搜索去重用）
        self._redis = None
        try:
            import redis as redis_lib
            self._redis = redis_lib.Redis(host="127.0.0.1", port=6379, db=0, socket_connect_timeout=2, decode_responses=True)
            self._redis.ping()
        except Exception:
            self._redis = None

    def _cache_key(self, prefix: str, *parts: str) -> str:
        import hashlib
        raw = ":".join(parts)
        return f"fm:{prefix}:{hashlib.md5(raw.encode()).hexdigest()[:16]}"

    def _cache_get(self, key: str) -> Optional[str]:
        if not self._redis:
            return None
        try:
            return self._redis.get(key)
        except Exception:
            return None

    def _cache_set(self, key: str, value: str, ttl: int = 300) -> None:
        if not self._redis:
            return
        try:
            self._redis.setex(key, ttl, value)
        except Exception:
            pass

    def _get_knowledge_base(self):
        """懒加载知识库（避免循环导入）"""
        if self._kb is None:
            from mind.knowledge import KnowledgeBase
            self._kb = KnowledgeBase()
        return self._kb

    def schema(self) -> List[Dict]:
        """返回 Claude Function Calling 格式的工具定义"""
        return self._registry.schema()

    def execute(self, name: str, args: dict) -> ToolResult:
        """执行工具，返回标准信封（含 status/result/latency_ms/care_signals）"""
        return self._registry.execute(name, args)

    def get_tool_meta(self, name: str) -> Dict:
        """获取工具元数据"""
        return self._registry.get_meta(name) or {}

    def without_tools(self, names: List[str]) -> "Toolkit":
        """返回一个新的 Toolkit，移除指定工具（用于子 Agent 禁止递归委托）。"""
        new = Toolkit.__new__(Toolkit)
        new.work_dir = self.work_dir
        new._kb = self._kb
        new._browser = self._browser
        new._redis = self._redis
        new._registry = ToolRegistry()
        for tool_name, method in self._registry._tools.items():
            if tool_name not in names:
                new._registry._tools[tool_name] = method
                new._registry._meta[tool_name] = self._registry._meta[tool_name]
        return new

    # ========== 文件 IO 工具 ==========

    def _normalize_path(self, path: str) -> str:
        """防止 LLM 传完整项目路径导致双重嵌套（如 work_dir=data/tasks/x-xxx，
        path=data/tasks/ChenPei_xxx/report.md → 应解析为 report.md）。"""
        if path.startswith("/"):
            return path
        work_str = str(self.work_dir)
        path_parts = Path(path).parts
        # 如果 path 以 data/tasks/<id>/... 开头，且 work_dir 已在 data/tasks/ 内，
        # 则剥掉 path 中的 data/tasks/<id>/ 前缀
        if (len(path_parts) >= 3 and path_parts[0] == "data" and path_parts[1] == "tasks"
                and "/data/tasks/" in work_str):
            if len(path_parts) > 3:
                return str(Path(*path_parts[3:]))
            return path_parts[-1] if len(path_parts) == 3 else ""
        return path

    @tool(name="read_file", domain="knowledge", category="io", local_only=True)
    def read_file(self, path: str) -> str:
        path = self._normalize_path(path)
        p = Path(path) if path.startswith("/") else self.work_dir / path
        if not p.exists():
            return f"文件不存在：{path}"
        try:
            return p.read_text(encoding="utf-8")[:5000]
        except Exception as e:
            return f"读取失败：{e}"

    @tool(name="write_file", domain="knowledge", category="io", local_only=True)
    def write_file(self, path: str, content: str, mode: str = "w") -> str:
        if not path:
            return "错误：缺少 path 参数（文件路径）。请提供要写入的文件相对路径，如 report.md"
        if content is None:
            return "错误：缺少 content 参数（文件内容）。请提供要写入的完整内容。"
        path = self._normalize_path(path)
        p = self.work_dir / path
        # 安全检查：禁止写到系统目录
        try:
            resolved = p.resolve()
            work_resolved = self.work_dir.resolve()
            if not str(resolved).startswith(str(work_resolved)):
                return "安全拦截：禁止写入工作目录之外的文件"
        except Exception:
            return "路径解析失败"

        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "a":
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
            return f"已追加写入 {path}（{len(content)} 字符）"
        p.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"

    @tool(name="list_dir", domain="admin", category="io", local_only=True)
    def list_dir(self, path: str = ".") -> str:
        path = self._normalize_path(path)
        p = self.work_dir / path
        if not p.exists():
            return "目录不存在"
        try:
            files = [f.name for f in p.iterdir()]
            return "文件：" + ", ".join(files) if files else "（空目录）"
        except Exception as e:
            return f"列出失败：{e}"

    @tool(name="md_to_pdf", domain="creation", category="io", local_only=True, timeout=30)
    def md_to_pdf(self, path: str, title: str = "") -> str:
        """将 Markdown 文件转换为 PDF，后台异步执行，不阻塞 Agent 循环。"""
        path = self._normalize_path(path)
        md_path = self.work_dir / path
        if not md_path.exists():
            return f"文件不存在：{path}"

        pdf_path = md_path.with_suffix(".pdf")
        job_key = str(pdf_path.resolve())

        with _pdf_jobs_lock:
            job = _pdf_jobs.get(job_key)
            if job and job.get("status") == "running":
                return f"PDF 正在后台生成中：{pdf_path.name}，请稍后在产出文件区查看。"

            _pdf_jobs[job_key] = {
                "status": "running",
                "submitted_at": datetime.now().isoformat(),
                "error": None,
            }

        def _worker():
            try:
                result = self._render_pdf_sync(md_path, pdf_path, title)
                with _pdf_jobs_lock:
                    _pdf_jobs[job_key]["status"] = "completed"
                    _pdf_jobs[job_key]["result"] = result
                    _pdf_jobs[job_key]["completed_at"] = datetime.now().isoformat()
                logger.info(f"[md_to_pdf] 后台生成完成: {pdf_path.name}")
            except Exception as e:
                logger.error(f"[md_to_pdf] 后台生成失败: {e}", exc_info=True)
                with _pdf_jobs_lock:
                    _pdf_jobs[job_key]["status"] = "failed"
                    _pdf_jobs[job_key]["error"] = str(e)

        thread = threading.Thread(target=_worker, name=f"pdf-{pdf_path.name}", daemon=True)
        thread.start()

        return (
            f"PDF 生成任务已提交后台：{pdf_path.name}。"
            "生成完成后会自动出现在右侧“产出文件”区，无需等待。"
        )

    def _render_pdf_sync(self, md_path: Path, pdf_path: Path, title: str = "") -> str:
        """实际同步 PDF 渲染逻辑（在后台线程中运行）。"""
        # 先尝试 WeasyPrint（高质量排版）
        try:
            import markdown as md_lib
            import re
            from weasyprint import HTML

            # macOS brew 库路径
            if os.path.exists("/opt/homebrew/lib"):
                os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

            md_text = md_path.read_text(encoding="utf-8")

            # 提取封面标题
            cover_title = title
            if not cover_title:
                for line in md_text.split("\n"):
                    if line.startswith("# ") and not line.startswith("##"):
                        cover_title = line[2:].strip()
                        break
            if not cover_title:
                cover_title = md_path.stem

            # 提取元信息
            meta_line = ""
            for line in md_text.split("\n"):
                stripped = line.strip().lstrip(">").strip()
                if any(k in stripped for k in ["研究时间", "所属领域", "研究对象类型"]):
                    meta_line = stripped
                    break

            # CSS 样式（与 deep-analysis skill 一致）
            css = """
            @page { size: A4; margin: 25mm 20mm 20mm 20mm;
                @top-center { content: "HEADER_TEXT"; font-size: 8pt; color: #95a5a6;
                    border-bottom: 0.5pt solid #ecf0f1; padding-bottom: 3mm; }
                @bottom-center { content: "第 " counter(page) " 页"; font-size: 8pt; color: #95a5a6;
                    border-top: 0.8pt solid #1a5276; padding-top: 2mm; }
            }
            @page :first { @top-center { content: none; } @bottom-center { content: none; } }
            body { font-family: "Droid Sans Fallback", Helvetica, Arial, sans-serif;
                font-size: 10.5pt; line-height: 1.75; color: #2c3e50; text-align: justify; }
            .cover { page-break-after: always; text-align: center; padding-top: 45%; }
            .cover h1 { font-size: 28pt; color: #1a5276; margin-bottom: 8mm; font-weight: bold; letter-spacing: 2pt; }
            .cover .subtitle { font-size: 14pt; color: #95a5a6; margin-bottom: 6mm; }
            .cover .meta { font-size: 11pt; color: #95a5a6; margin-bottom: 4mm; }
            .cover .divider { width: 60%; margin: 8mm auto; border: none; border-top: 1.5pt solid #1a5276; }
            h1 { font-size: 20pt; color: #1a5276; margin-top: 16mm; margin-bottom: 6mm;
                padding-bottom: 3mm; border-bottom: 2pt solid #1a5276; page-break-before: always; font-weight: bold; }
            h2 { font-size: 14pt; color: #1e8449; margin-top: 10mm; margin-bottom: 5mm; font-weight: bold; }
            h3 { font-size: 12pt; color: #2e86c1; margin-top: 6mm; margin-bottom: 3mm; font-weight: bold; }
            h4 { font-size: 11pt; color: #5b2c6f; margin-top: 5mm; margin-bottom: 2mm; font-weight: bold; }
            p { margin-top: 1.5mm; margin-bottom: 1.5mm; orphans: 3; widows: 3; }
            blockquote { margin: 4mm 0; padding: 4mm 4mm 4mm 10mm; background: #f8f9fa;
                border-left: 3pt solid #1a5276; color: #5d6d7e; font-size: 10pt; }
            blockquote p { margin: 1mm 0; }
            strong, b { font-weight: bold; color: #1a252f; }
            table { width: 100%; border-collapse: collapse; margin: 4mm 0; font-size: 9.5pt; }
            thead th { background: #1a5276; color: white; padding: 3mm; text-align: left; font-weight: bold; }
            tbody td { padding: 2.5mm 3mm; border-bottom: 0.5pt solid #bdc3c7; }
            tbody tr:nth-child(even) { background: #f8f9fa; }
            hr { border: none; border-top: 0.5pt solid #bdc3c7; margin: 4mm 0; }
            ul, ol { margin: 2mm 0; padding-left: 8mm; }
            li { margin-bottom: 1mm; }
            a { color: #2e86c1; text-decoration: none; }
            """.replace("HEADER_TEXT", f"{cover_title}  |  横纵分析法深度研究报告")

            # Markdown → HTML
            html_body = md_lib.markdown(md_text, extensions=["tables", "fenced_code", "nl2br"], output_format="html5")

            # 移除正文第一个 h1（用于封面）
            first_h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html_body, re.DOTALL)
            if first_h1:
                html_body = html_body.replace(first_h1.group(0), '', 1)

            cover_html = f'''<div class="cover">
                <h1 style="page-break-before: avoid; border: none;">{cover_title}</h1>
                <div class="subtitle">横纵分析法深度研究报告</div>
                {"<div class='meta'>" + meta_line + "</div>" if meta_line else ""}
                <hr class="divider">
                <div class="meta">作者: 销销</div>
            </div>'''

            full_html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><style>{css}</style></head>
            <body>{cover_html}{html_body}</body></html>"""

            HTML(string=full_html).write_pdf(str(pdf_path))
            size_kb = pdf_path.stat().st_size // 1024
            return f"PDF 已生成：{pdf_path.name}（{size_kb}KB，WeasyPrint排版）"

        except Exception as we_err:
            logger.warning(f"WeasyPrint 失败，回退到 fpdf2: {we_err}")

        # 回退：fpdf2（极简排版）
        font_candidates = [
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]
        font_path = None
        for fp in font_candidates:
            if Path(fp).exists():
                font_path = fp
                break
        if not font_path:
            raise RuntimeError("未找到系统可用的中文字体，无法生成 PDF。")

        try:
            from fpdf import FPDF
        except ImportError as e:
            raise RuntimeError("fpdf2 未安装。") from e

        md_text = md_path.read_text(encoding="utf-8")
        lines = md_text.split("\n")
        cover_title = title or md_path.stem
        for line in lines:
            if line.startswith("# ") and not line.startswith("##"):
                cover_title = line[2:].strip()
                break

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font("cn", "", font_path)
        pdf.add_font("cn", "B", font_path)
        pdf.add_page()
        pdf.set_font("cn", "B", 22)
        pdf.ln(65)
        pdf.cell(0, 14, cover_title, new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.add_page()
        pdf.set_font("cn", "", 10)
        for raw in lines:
            line = raw.rstrip()
            if not line:
                pdf.ln(3); continue
            if line.startswith("# ") and not line.startswith("##"):
                pdf.set_font("cn", "B", 14); pdf.ln(5)
                pdf.multi_cell(0, 8, line[2:].strip(), ln=1)
                pdf.set_font("cn", "", 10)
            elif line.startswith("## "):
                pdf.set_font("cn", "B", 12); pdf.ln(4)
                pdf.multi_cell(0, 7, line[3:].strip(), ln=1)
                pdf.set_font("cn", "", 10)
            elif line.startswith("### "):
                pdf.set_font("cn", "B", 11); pdf.ln(3)
                pdf.multi_cell(0, 6, line[4:].strip(), ln=1)
                pdf.set_font("cn", "", 10)
            elif line.startswith("#### "):
                pdf.set_font("cn", "B", 10); pdf.ln(2)
                pdf.multi_cell(0, 5, line[5:].strip(), ln=1)
                pdf.set_font("cn", "", 10)
            elif line.startswith("---") or line.startswith("***"):
                pdf.ln(2)
            elif line.startswith("|") and "---" not in line:
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if any(cells):
                    pdf.multi_cell(0, 4, " | ".join(c for c in cells if c), ln=1)
            elif line.startswith("* ") or line.startswith("- "):
                pdf.set_x(pdf.l_margin + 4)
                pdf.multi_cell(0, 4, "• " + line[2:].strip().replace("**", ""), ln=1)
            elif line.startswith("> "):
                pdf.set_font("cn", "B", 9)
                pdf.set_text_color(80, 80, 80)
                pdf.set_x(pdf.l_margin + 3)
                pdf.multi_cell(0, 4, line[2:].strip().replace("**", ""), ln=1)
                pdf.set_text_color(0, 0, 0)
                pdf.set_font("cn", "", 10)
            else:
                pdf.multi_cell(0, 4, line.replace("**", ""), ln=1)
        pdf.output(str(pdf_path))
        size_kb = pdf_path.stat().st_size // 1024
        return f"PDF 已生成：{pdf_path.name}（{size_kb}KB，fpdf2回退）"

    # ========== 知识库工具 ==========

    @tool(name="search_knowledge", domain="knowledge", category="retrieval", local_only=True, care_scanner="enabled")
    def search_knowledge(self, query: str) -> str:
        """
        语义搜索知识库（L0/L1/L2 分层检索，OpenViking 启发 #1）
        如果向量库未就绪或搜索失败，回退到关键词匹配
        """
        # 优先使用向量检索
        try:
            kb = self._get_knowledge_base()
            results = kb.search(query, top_k=5, min_similarity=0.25)
            if results:
                lines = []
                for r in results:
                    sim = r.get("similarity", 0)
                    l0 = r.get("l0", "")[:60]
                    text = r.get("chunk_text", "")[:400]
                    header = f"[相关度{sim:.0%}]"
                    if l0:
                        header += f" {l0}..."
                    lines.append(f"{header}\n{text}")
                return "知识库检索结果：\n" + "\n---\n".join(lines)
        except Exception as e:
            logger.warning(f"向量检索失败，回退到关键词匹配: {e}")

        # 回退：关键词匹配（Phase 1 兼容）
        return self._fallback_keyword_search(query)

    def _fallback_keyword_search(self, query: str) -> str:
        """关键词匹配回退方案"""
        keywords = [k for k in query.lower().split() if len(k) >= 2]
        if not keywords:
            keywords = [query.lower()]

        results = []
        for f in KNOWLEDGE_DIR.rglob("*"):
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                text_lower = text.lower()
                if any(k in text_lower for k in keywords):
                    idx = text_lower.find(keywords[0])
                    snippet = text[max(0, idx-100):idx+300]
                    results.append(f"【{f.name}】...{snippet}...")
            except Exception:
                pass

        if not results:
            return "（知识库无匹配）"
        return "关键词匹配结果：\n" + "\n---\n".join(results[:3])

    @tool(name="sync_wechat_digest", domain="knowledge", category="retrieval", local_only=True, timeout=300)
    def sync_wechat_digest(self, kb_path: str = "") -> str:
        """同步 wechat-digest 知识库到销销向量库（增量更新）"""
        from mind import wechat_digest
        path = kb_path or None
        stats = wechat_digest.sync(path)
        return json.dumps(stats, ensure_ascii=False)

    @tool(name="search_industry_news", domain="knowledge", category="retrieval", local_only=True)
    def search_industry_news(self, query: str, top_k: int = 5) -> str:
        """在 wechat-digest 行业资讯库中语义搜索相关文章"""
        from mind import wechat_digest
        results = wechat_digest.search(query, top_k=top_k)
        if not results:
            return "（行业资讯库暂无匹配，可尝试先用 sync_wechat_digest 同步）"

        lines = ["行业资讯库匹配结果："]
        for r in results:
            header = f"《{r['title']}》｜{r['account']}｜{r['publish_date']}｜相关度 {r['similarity']:.0%}"
            summary = r["summary"] or r["snippet"]
            summary = summary.replace("\n", " ")
            link = r["link"] or "（无链接）"
            lines.append(f"\n{header}\n{summary}\n来源：{link}")
        return "\n".join(lines)

    @tool(name="get_wechat_kb_stats", domain="knowledge", category="retrieval", local_only=True)
    def get_wechat_kb_stats(self) -> str:
        """查看已同步的 wechat-digest 知识库统计"""
        from mind import wechat_digest
        return json.dumps(wechat_digest.get_stats(), ensure_ascii=False)

    # ========== 销售数据工具 ==========

    def _sales_memory(self):
        """懒加载销售记忆访问器（避免循环导入）"""
        from mind.memory import Memory
        return Memory("system", "system")

    def _format_records(self, records: list) -> str:
        if not records:
            return "（无记录）"
        return json.dumps(records, ensure_ascii=False, default=str, indent=2)

    @tool(name="get_account", domain="sales", category="crm", local_only=True)
    def get_account(self, name: str) -> str:
        """按名称模糊匹配客户公司，返回公司详情（含已保存的研究摘要）"""
        m = self._sales_memory()
        account = m.get_account_by_name(name)
        if not account:
            return f"未找到名为「{name}」的客户公司。"
        return self._format_records([account])

    @tool(name="save_account_research", domain="sales", category="crm", local_only=True)
    def save_account_research(self, name: str, summary: str) -> str:
        """保存客户研究结果到 CRM；如该公司不存在会自动创建"""
        m = self._sales_memory()
        ok = m.save_account_research(name, summary)
        return "已保存客户研究结果。" if ok else "保存失败。"

    @tool(name="list_accounts", domain="sales", category="crm", local_only=True)
    def list_accounts(self, owner_id: str = "") -> str:
        """列出客户公司；可指定 owner_id 过滤，留空返回全部"""
        m = self._sales_memory()
        accounts = m.list_accounts(owner_id=owner_id or None, limit=50)
        return self._format_records(accounts)

    @tool(name="get_contacts", domain="sales", category="crm", local_only=True)
    def get_contacts(self, account_name: str) -> str:
        """查询某客户公司下的联系人"""
        m = self._sales_memory()
        account = m.get_account_by_name(account_name)
        if not account:
            return f"未找到名为「{account_name}」的客户公司。"
        contacts = m.list_contacts_by_account(account["account_id"])
        return self._format_records(contacts)

    @tool(name="get_deals", domain="sales", category="crm", local_only=True)
    def get_deals(self, account_name: str) -> str:
        """查询某客户公司下的商机"""
        m = self._sales_memory()
        account = m.get_account_by_name(account_name)
        if not account:
            return f"未找到名为「{account_name}」的客户公司。"
        deals = m.list_deals_by_account(account["account_id"])
        return self._format_records(deals)

    @tool(name="get_activities", domain="sales", category="crm", local_only=True)
    def get_activities(self, account_name: str = "", contact_name: str = "", limit: int = 10) -> str:
        """查询客户或联系人的最近活动记录"""
        m = self._sales_memory()
        entity_type = None
        entity_id = None
        if account_name:
            account = m.get_account_by_name(account_name)
            if account:
                entity_type, entity_id = "account", account["account_id"]
        if contact_name and not entity_id:
            # 按名称在所有联系人中模糊匹配
            with m.conn.cursor(cursor_factory=RealDictCursor) as c:
                c.execute("SELECT * FROM contacts WHERE name ILIKE %s LIMIT 1", (f"%{contact_name}%",))
                contact = c.fetchone()
            if contact:
                entity_type, entity_id = "contact", contact["contact_id"]
        if not entity_id:
            return "未找到对应的客户或联系人。"
        activities = m.list_activities(entity_type=entity_type, entity_id=entity_id, limit=limit)
        return self._format_records(activities)

    @tool(name="log_activity", domain="sales", category="crm", local_only=True)
    def log_activity(self, entity_type: str, entity_id: str, activity_type: str,
                     content: str, direction: str = "outbound", owner_id: str = "") -> str:
        """记录一次销售活动（电话、会议、邮件、微信、笔记等）"""
        import uuid
        m = self._sales_memory()
        activity_id = f"act_{uuid.uuid4().hex[:8]}"
        ok = m.log_activity(
            activity_id=activity_id,
            entity_type=entity_type,
            entity_id=entity_id,
            activity_type=activity_type,
            content=content,
            direction=direction,
            owner_id=owner_id or "system",
        )
        return "活动已记录" if ok else "记录活动失败"

    # ========== 管理工具 ==========

    @tool(name="get_time", domain="admin", category="info", local_only=True)
    def get_time(self) -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")

    # ========== 世界域工具 ==========

    # ========== 浏览器工具（世界域）==========

    def _get_sandbox(self):
        """懒加载沙箱"""
        if not hasattr(self, '_sandbox'):
            from mind.sandbox import Sandbox
            self._sandbox = Sandbox(work_dir=self.work_dir)
        return self._sandbox

    # ========== 代码执行工具（Phase 2.5.1）==========

    @tool(name="execute_code", domain="creation", category="sandbox", timeout=35, care_scanner="enabled")
    def execute_code(self, code: str, language: str = "python") -> str:
        """
        执行 Python 代码，返回运行结果
        适合：数据处理、文件生成、格式转换、简单计算
        安全：禁止 rm/chmod/system/subprocess/eval/exec，超时30秒
        """
        if language != "python":
            return "当前只支持 Python 代码执行"
        try:
            sb = self._get_sandbox()
            result = sb.execute_python(code)
            status = result["status"]
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            rc = result.get("returncode", 0)

            lines = [f"执行状态: {status}"]
            if stdout:
                lines.append(f"标准输出:\n{stdout}")
            if stderr:
                lines.append(f"错误输出:\n{stderr}")
            if rc != 0 and not stderr:
                lines.append(f"退出码: {rc}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"代码执行工具失败: {e}")
            return f"代码执行失败: {e}"

    @tool(name="pip_install", domain="creation", category="sandbox", timeout=125)
    def pip_install(self, package: str = None, packages: list = None) -> str:
        """
        安装 PyPI 包
        适合：安装生成 PDF/PPT/文档所需的库
        安全：禁止安装系统/网络相关包
        """
        # 兼容模型生成的 package 或 packages 参数
        targets = []
        if package:
            targets.append(package)
        if packages:
            if isinstance(packages, str):
                targets.append(packages)
            elif isinstance(packages, (list, tuple)):
                targets.extend(packages)
        if not targets:
            return "未指定要安装的包"

        results = []
        for pkg in targets:
            try:
                sb = self._get_sandbox()
                result = sb.pip_install(pkg)
                status = result["status"]
                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")
                if status == "success":
                    results.append(f"{pkg}: 安装成功 ✅\n{stdout[:300]}")
                else:
                    results.append(f"{pkg}: 安装失败 ({status})\n{stderr[:500]}")
            except Exception as e:
                results.append(f"{pkg}: pip 安装失败: {e}")
        return "\n---\n".join(results)

    # ========== 技能安装工具（Phase 2.5.3）==========

    @tool(name="install_skill", domain="admin", category="skill", timeout=20)
    def install_skill(self, source: str, skill_name: str = None) -> str:
        """
        从 URL 或文本安装新 skill
        适合：用户发送 skill 链接或 skill 文本时调用
        - source: URL 链接 或 skill 的完整文本内容
        - skill_name: 可选，指定 skill 文件名（不含 .md）
        """
        from mind.skill_installer import install_from_url, install_from_text

        # 判断是 URL 还是纯文本
        is_url = source.strip().startswith(("http://", "https://"))

        try:
            if is_url:
                result = install_from_url(source.strip())
            else:
                result = install_from_text(source, skill_name=skill_name)

            if result["status"] == "success":
                return f"{result['message']}\n\n安装后可以直接使用，比如：'用 {skill_name or '新skill'} 帮我做xxx'"
            return f"安装失败 ❌\n{result['message']}"
        except Exception as e:
            logger.error(f"技能安装失败: {e}")
            return f"技能安装失败: {e}"

    @tool(name="list_skills", domain="admin", category="skill", timeout=3)
    def list_skills(self) -> str:
        """列出已安装的所有 skills"""
        from mind.skill_installer import list_installed_skills
        return list_installed_skills()

    @tool(name="set_reminder", domain="care", category="schedule", timeout=5)
    def set_reminder(self, user_id: str = None, content: str = None, remind_time: str = None) -> str:
        """
        设置一次性提醒
        - user_id: 用户ID（如 ChenPei）
        - content: 提醒内容
        - remind_time: 时间，支持 "18:00"、"6:00"、"2026-04-30 18:00" 等格式
        """
        from datetime import datetime, timedelta
        from mind.scheduler import add_reminder

        if not user_id or not content or not remind_time:
            return "参数不全，需要提供 user_id、content、remind_time"

        now = datetime.now()
        remind_at = None

        # 尝试 "18:00" 或 "6点" 格式
        try:
            t = remind_time.strip().replace("点", ":").replace("分", "").replace("半", ":30")
            if ":" in t:
                parts = t.split(":")
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 and parts[1] else 0
                remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if remind_at < now:
                    remind_at += timedelta(days=1)
        except Exception:
            pass

        # 尝试 ISO 格式
        if remind_at is None:
            try:
                remind_at = datetime.fromisoformat(remind_time.replace(" ", "T"))
            except Exception:
                pass

        if remind_at is None:
            return f"时间格式不支持：{remind_time}，请用 \"18:00\" 或 \"2026-04-30 18:00\" 格式"

        job_id = add_reminder(user_id, content, remind_at)
        return f"已设置提醒：{content}，时间 {remind_at.strftime('%m月%d日 %H:%M')}"

    # ========== 浏览器工具（世界域）==========

    def _get_browser(self):
        """懒加载浏览器（通过 CDP Proxy）"""
        if self._browser is None:
            from mind.browser import Browser
            self._browser = Browser(work_dir=self.work_dir)
        return self._browser

    @tool(name="browse_open", domain="world", category="browser", timeout=10)
    def browse_open(self, url: str) -> str:
        """打开网页并返回内容摘要"""
        try:
            browser = self._get_browser()
            return browser.open(url)
        except Exception as e:
            logger.error(f"打开网页失败: {e}")
            return f"打开网页失败：{e}"

    @tool(name="browse_click", domain="world", category="browser", timeout=5)
    def browse_click(self, selector: str) -> str:
        """点击页面元素"""
        try:
            browser = self._get_browser()
            return browser.click(selector)
        except Exception as e:
            return f"点击失败：{e}"

    @tool(name="browse_fill", domain="world", category="browser", timeout=5)
    def browse_fill(self, selector: str, text: str) -> str:
        """在输入框填写内容"""
        try:
            browser = self._get_browser()
            return browser.fill(selector, text)
        except Exception as e:
            return f"填写失败：{e}"

    @tool(name="browse_screenshot", domain="world", category="browser", timeout=8)
    def browse_screenshot(self, filename: str = None) -> str:
        """截图保存到工作目录"""
        try:
            browser = self._get_browser()
            return browser.screenshot(filename)
        except Exception as e:
            return f"截图失败：{e}"

    @tool(name="browse_scroll", domain="world", category="browser", timeout=3)
    def browse_scroll(self, direction: str = "down") -> str:
        """滚动页面"""
        try:
            browser = self._get_browser()
            return browser.scroll(direction)
        except Exception as e:
            return f"滚动失败：{e}"

    @tool(name="browse_text", domain="world", category="browser", timeout=5)
    def browse_text(self, selector: str = "body") -> str:
        """提取页面文字"""
        try:
            browser = self._get_browser()
            return browser.get_text(selector)
        except Exception as e:
            return f"提取文字失败：{e}"

    # ========== 网页获取工具（web-access 适配）==========

    @tool(name="fetch_webpage", domain="world", category="browser", timeout=10)
    def fetch_webpage(self, url: str) -> str:
        """
        直接 HTTP 获取网页原始内容（不经过浏览器渲染）。
        适合：文章、博客、公告、静态页面等以文字为主的页面。
        比 browse_open 更快更省 token，但不支持 JavaScript 动态内容。
        """
        try:
            import requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            resp.raise_for_status()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            # 移除脚本和样式
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            title = soup.title.string.strip() if soup.title else ""
            body = soup.get_text(separator="\n", strip=True)
            # 压缩空行
            lines = [l for l in body.splitlines() if l.strip()]
            text = "\n".join(lines[:200])  # 限制长度
            return f"标题：{title}\n\n内容：\n{text}"
        except ImportError as e:
            return f"缺少依赖：{e}（请安装 beautifulsoup4）"
        except Exception as e:
            logger.error(f"fetch_webpage 失败: {e}")
            return f"获取网页失败：{e}"

    @tool(name="jina_reader", domain="world", category="browser", timeout=10)
    def jina_reader(self, url: str) -> str:
        """
        用 Jina AI Reader 将网页转为 Markdown，大幅节省 token。
        适合：文章、博客、文档、PDF 等以正文为核心的页面。
        限 20 RPM，对数据面板、商品页等非文章结构可能提取错误。
        调用方式：在 URL 前加 https://r.jina.ai/http://
        """
        try:
            import requests
            jina_url = f"https://r.jina.ai/http://{url.replace('https://', '').replace('http://', '')}"
            resp = requests.get(jina_url, timeout=15, headers={"User-Agent": "SalesMind/1.0"})
            resp.raise_for_status()
            content = resp.text.strip()
            if not content or len(content) < 50:
                return "Jina Reader 未返回有效内容，该页面可能不支持。"
            return f"[Jina Reader] {url}\n\n{content[:8000]}"
        except Exception as e:
            logger.error(f"jina_reader 失败: {e}")
            return f"Jina Reader 失败：{e}"

    @tool(name="find_chrome_url", domain="world", category="browser", timeout=8)
    def find_chrome_url(self, keywords: str, limit: int = 10, since: str = None) -> str:
        """
        从本地 Chrome 书签/历史中检索 URL。
        用于定位公网搜索不到的目标（组织内部系统、之前访问过的页面等）。
        参数：
          keywords: 搜索关键词（空格分隔，多词 AND 匹配 title + url）
          limit: 返回条数上限，默认 10
          since: 时间窗，如 "7d" / "24h" / "2026-04-01"（仅作用于历史）
        """
        try:
            script = Path.home() / ".claude/skills/web-access/scripts/find-url.mjs"
            if not script.exists():
                return "find-url 脚本未找到，请确认 web-access skill 已安装。"
            cmd = ["node", str(script)]
            for kw in keywords.split():
                cmd.append(kw)
            if limit != 10:
                cmd.extend(["--limit", str(limit)])
            if since:
                cmd.extend(["--since", since])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0 and not result.stdout:
                return f"检索失败：{result.stderr}"
            output = result.stdout.strip()
            if not output:
                return f"未在 Chrome 书签/历史中找到匹配「{keywords}」的记录。"
            return output
        except subprocess.TimeoutExpired:
            return "检索超时，Chrome 历史数据库可能过大。"
        except Exception as e:
            logger.error(f"find_chrome_url 失败: {e}")
            return f"检索失败：{e}"

    @tool(name="search_web", domain="world", category="browser", timeout=15)
    def search_web(self, query: str, max_results: int = 5) -> str:
        """
        联网搜索实时信息（SearXNG 聚合搜索，国内引擎）
        适合：热点新闻、实时信息、公开资料查询、政策检索
        底层：同时搜索 general（百度/必应/360/搜狗）和 news（搜狗微信/必应新闻），合并去重
        缓存：Redis 缓存 5 分钟，重复查询直接返回缓存结果
        """
        import requests
        import re
        cache_key = self._cache_key("search_web", query, str(max_results))
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        try:
            url = _searxng_search_url()

            def _search_category(cat: str, timeout: int = 8) -> tuple:
                params = {
                    "q": query,
                    "format": "json",
                    "language": "zh-CN",
                    "safesearch": 2,
                    "categories": cat,
                }
                try:
                    resp = requests.get(url, params=params, timeout=timeout, proxies={"http": None, "https": None})
                    resp.raise_for_status()
                    raw_text = resp.text
                    results = resp.json().get("results", [])
                    # 检测反爬虫特征：SearXNG 引擎被封时 results 为空，但原始响应可能含验证码
                    blocked = any(k in raw_text for k in ("wappass.baidu.com", "antispider", "验证码", "captcha"))
                    return results, blocked
                except Exception:
                    return [], False

            # 同时搜 general + news，合并去重
            gen_results, gen_blocked = _search_category("general")
            news_results, news_blocked = _search_category("news")
            searxng_blocked = gen_blocked or news_blocked

            seen_urls = set()
            merged = []
            for r in gen_results + news_results:
                r_url = r.get("url", "")
                if r_url and r_url not in seen_urls:
                    seen_urls.add(r_url)
                    merged.append(r)

            # 质量检查：如果查询含中文，结果标题中应有中文关键词匹配
            cn_keywords = re.findall(r'[一-鿿]{2,}', query)
            en_keywords = [w.lower() for w in re.findall(r'[a-zA-Z]{3,}', query.replace('"', '').replace("'", ''))]

            def _is_relevant(r: dict) -> bool:
                text = (r.get("title", "") + " " + r.get("content", "")).lower()
                if cn_keywords and any(kw in text for kw in cn_keywords):
                    return True
                if en_keywords and any(kw in text for kw in en_keywords):
                    return True
                # 查询不含中文且无英文关键词时，不过滤
                return not cn_keywords and not en_keywords

            # 优先保留相关结果，垃圾结果排后面
            relevant = [r for r in merged if _is_relevant(r)]
            irrelevant = [r for r in merged if not _is_relevant(r)]
            merged = relevant + irrelevant

            results = merged[:max_results]

            # Fallback：SearXNG 被反爬虫拦截或返回空时，用 Chrome 打开百度搜索页提取结果
            if (not results or searxng_blocked) and cn_keywords:
                try:
                    baidu_url = f"https://www.baidu.com/s?wd={requests.utils.quote(query)}"
                    browse_result = self.browse_open(baidu_url)
                    if browse_result and "百度搜索" in str(browse_result):
                        text = str(browse_result)
                        # 提取搜索结果：找"百度为您找到以下结果"之后的文本
                        marker = "百度为您找到以下结果"
                        if marker in text:
                            snippet = text.split(marker, 1)[1][:3000]
                            results.append({
                                "title": f"百度搜索：{query}",
                                "url": baidu_url,
                                "content": snippet,
                                "engine": "baidu_browser",
                            })
                            logger.info(f"search_web fallback 到 Chrome 百度搜索，获取内容 {len(snippet)} 字符")
                except Exception as e:
                    logger.warning(f"Chrome fallback 搜索失败: {e}")

            if not results:
                return f"搜索「{query}」未找到结果。"

            # 质量提示（fallback 到浏览器时跳过，因为内容是直接从搜索页抓取的）
            quality_hint = ""
            has_fallback = any(r.get("engine") == "baidu_browser" for r in results)
            if not has_fallback and len(relevant) < max_results * 0.5 and (cn_keywords or en_keywords):
                quality_hint = (
                    "\n\n⚠️ 搜索结果质量较差，大部分结果与查询词不相关（可能是搜索引擎返回了无关内容）。"
                    "建议：1) 换用 browse_open 直接打开 bing.com 或 baidu.com 手动搜索；"
                    "2) 或尝试不同关键词组合。"
                )

            lines = [f"搜索：{query}"]
            for i, r in enumerate(results, 1):
                title = r.get("title", "")
                content = r.get("content", "")[:250]
                r_url = r.get("url", "")
                engine = r.get("engine", "")
                lines.append(f"{i}. {title}\n   {content}...\n   来源：{engine} | {r_url}")
            result_text = "\n\n".join(lines) + quality_hint
            self._cache_set(cache_key, result_text, ttl=300)
            return result_text
        except requests.exceptions.ConnectionError:
            logger.error("SearXNG 连接失败")
            return "搜索引擎暂时不可用（SearXNG 未启动），我先用知道的信息回答您。"
        except Exception as e:
            logger.error(f"联网搜索失败: {e}")
            return f"搜索出了点问题：{e}"

    @tool(name="multi_search", domain="world", category="browser", timeout=25)
    def multi_search(self, queries: list, max_results: int = 3) -> str:
        """
        并行执行多个搜索查询（Intent Analysis 多子查询）
        适合：复杂问题需要拆成多个角度同时搜索，一次获取全面信息
        示例 queries: ["鄂尔多斯5月天气", "鄂尔多斯带娃旅游注意事项", "鄂尔多斯机场交通"]
        缓存：Redis 缓存 5 分钟，重复查询直接返回缓存结果
        """
        import requests
        from concurrent.futures import ThreadPoolExecutor

        cache_key = self._cache_key("multi_search", "|".join(sorted(queries)), str(max_results))
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        url = "http://127.0.0.1:8080/search"

        def _search_one(query: str) -> str:
            def _cat(cat: str, timeout: int = 8) -> list:
                params = {
                    "q": query,
                    "format": "json",
                    "language": "zh-CN",
                    "safesearch": 2,
                    "categories": cat,
                }
                try:
                    resp = requests.get(url, params=params, timeout=timeout, proxies={"http": None, "https": None})
                    resp.raise_for_status()
                    return resp.json().get("results", [])
                except Exception:
                    return []

            gen = _cat("general")
            news = _cat("news")
            seen = set()
            merged = []
            for r in gen + news:
                u = r.get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    merged.append(r)

            results = merged[:max_results]
            if not results:
                return f"【{query}】未找到结果"
            lines = [f"【{query}】"]
            for i, r in enumerate(results, 1):
                title = r.get("title", "")
                content = r.get("content", "")[:250]
                src = r.get("engine", "")
                lines.append(f"{i}. {title}\n   {content}...\n   来源：{src}")
            return "\n".join(lines)

        with ThreadPoolExecutor(max_workers=min(len(queries), 5)) as executor:
            futures = [executor.submit(_search_one, q) for q in queries]
            results = [f.result() for f in futures]

        result_text = "\n\n".join(results)
        self._cache_set(cache_key, result_text, ttl=300)
        return result_text

    @tool(name="search_and_fetch", domain="world", category="browser", timeout=45)
    def search_and_fetch(self, query: str, max_results: int = 3, fetch_count: int = 3) -> str:
        """
        搜索并自动抓取关键文章全文
        适合：需要深度信息的场景（研究报告、背景调查、概念理解）
        流程：同时搜索 general+news → 取前 fetch_count 条 → 抓取全文 → 返回摘要+原文
        注意：耗时较长（15-40秒），仅当需要深度信息时使用
        缓存：Redis 缓存 5 分钟，重复查询直接返回缓存结果
        """
        import requests
        from concurrent.futures import ThreadPoolExecutor

        cache_key = self._cache_key("search_and_fetch", query, str(max_results), str(fetch_count))
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        url = "http://127.0.0.1:8080/search"

        def _cat(cat: str) -> list:
            params = {
                "q": query,
                "format": "json",
                "language": "zh-CN",
                "safesearch": 2,
                "categories": cat,
            }
            try:
                resp = requests.get(url, params=params, timeout=10, proxies={"http": None, "https": None})
                resp.raise_for_status()
                return resp.json().get("results", [])
            except Exception:
                return []

        # 搜索：general + news 合并去重
        gen = _cat("general")
        news = _cat("news")
        seen = set()
        merged = []
        for r in gen + news:
            u = r.get("url", "")
            if u and u not in seen:
                seen.add(u)
                merged.append(r)

        search_results = merged[:max_results]
        if not search_results:
            return f"搜索「{query}」未找到结果。"

        lines = [f"搜索：{query}"]
        for i, r in enumerate(search_results, 1):
            lines.append(f"{i}. {r.get('title', '')}\n   来源：{r.get('engine', '')} | {r.get('url', '')}")

        # 抓取前 fetch_count 条原文
        fetch_targets = merged[:fetch_count]
        if fetch_targets:
            lines.append("\n--- 抓取原文 ---")

        def _fetch(url: str) -> str:
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                r = requests.get(url, timeout=15, headers=headers, proxies={"http": None, "https": None})
                r.raise_for_status()
                # 简单提取文本（去HTML标签）
                text = r.text
                # 尝试从常见内容标签提取
                import re
                for pattern in [r'<article[^>]*>(.*?)</article>', r'<main[^>]*>(.*?)</main>',
                                r'<div class="content[^"]*"[^>]*>(.*?)</div>', r'<div id="content[^"]*"[^>]*>(.*?)</div>']:
                    m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                    if m:
                        text = m.group(1)
                        break
                # 去掉所有标签
                text = re.sub(r'<[^>]+>', ' ', text)
                # 合并空白
                text = re.sub(r'\s+', ' ', text).strip()
                # 限制长度
                if len(text) > 8000:
                    text = text[:8000] + "...（内容过长已截断）"
                return text
            except Exception as e:
                return f"[抓取失败：{e}]"

        with ThreadPoolExecutor(max_workers=min(len(fetch_targets), 3)) as executor:
            futures = {executor.submit(_fetch, r.get("url", "")): r for r in fetch_targets}
            for f in futures:
                r = futures[f]
                content = f.result()
                lines.append(f"\n【{r.get('title', '')}】\n来源：{r.get('url', '')}\n\n{content}")

        result_text = "\n".join(lines)
        self._cache_set(cache_key, result_text, ttl=300)
        return result_text

    @tool(name="search_zhihu", domain="world", category="browser", timeout=15)
    def search_zhihu(self, query: str, max_results: int = 5) -> str:
        """
        知乎内容搜索（通过搜索引擎 site:zhihu.com 定向抓取）
        适合：找知乎上的观点、经验分享、专业讨论
        缓存：Redis 缓存 5 分钟
        """
        import requests

        cache_key = self._cache_key("search_zhihu", query, str(max_results))
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        try:
            url = _searxng_search_url()
            params = {
                "q": query,
                "format": "json",
                "language": "zh-CN",
                "safesearch": 2,
                "categories": "general",
            }
            resp = requests.get(url, params=params, timeout=10, proxies={"http": None, "https": None})
            resp.raise_for_status()
            results = resp.json().get("results", [])
            # 过滤出知乎链接（site:zhihu.com 在 SearXNG 中不生效，改为结果过滤）
            zhihu_results = [r for r in results if "zhihu.com" in r.get("url", "")]
            zhihu_results = zhihu_results[:max_results]

            if not zhihu_results:
                return f"知乎上未找到「{query}」相关内容。"

            lines = [f"知乎搜索：{query}"]
            for i, r in enumerate(zhihu_results, 1):
                title = r.get("title", "").replace(" - 知乎", "").strip()
                content = r.get("content", "")[:250]
                url = r.get("url", "")
                lines.append(f"{i}. {title}\n   {content}...\n   {url}")
            result_text = "\n\n".join(lines)
            self._cache_set(cache_key, result_text, ttl=300)
            return result_text
        except Exception as e:
            logger.error(f"知乎搜索失败: {e}")
            return f"知乎搜索出错：{e}"

    # ========== peistock 股票数据工具 ===========

    @tool(name="query_peistock", domain="world", category="finance", timeout=15, care_scanner="enabled")
    def query_peistock(self, endpoint: str = "stock", code: str = None, codes: list = None) -> str:
        """
        查询 peistock 股票数据（HTTP API）
        endpoint: stock | signals | watchlist | scan | health
        code: 股票代码（endpoint=stock 时必填，如 600519、00700）
        codes: 股票代码列表（endpoint=scan 时使用，最多10只）
        """
        base_url = os.getenv("PEISTOCK_API_URL", "http://localhost:3457")
        if base_url.endswith("/"):
            base_url = base_url[:-1]

        try:
            if endpoint == "stock":
                if not code:
                    return "参数错误：查询单股需要传入 code"
                url = f"{base_url}/api/stock/{code}"
                req = urllib.request.Request(url, method="GET")

            elif endpoint == "signals":
                url = f"{base_url}/api/signals/latest"
                req = urllib.request.Request(url, method="GET")

            elif endpoint == "watchlist":
                url = f"{base_url}/api/watchlist"
                req = urllib.request.Request(url, method="GET")

            elif endpoint == "scan":
                url = f"{base_url}/api/scan"
                payload = json.dumps({"codes": codes or []}).encode("utf-8")
                req = urllib.request.Request(
                    url, data=payload, method="POST",
                    headers={"Content-Type": "application/json"}
                )

            elif endpoint == "health":
                url = f"{base_url}/health"
                req = urllib.request.Request(url, method="GET")

            else:
                return f"不支持的 endpoint：{endpoint}，可选：stock, signals, watchlist, scan, health"

            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # 格式化输出
            if endpoint == "stock":
                if "error" in data:
                    return f"查询失败：{data['error']}"
                lines = [
                    f"【{data.get('name', data.get('code', ''))} {data.get('code', '')}】",
                    f"价格：{data.get('price', '—')}  涨跌：{data.get('changePercent', '—')}%",
                    f"日期：{data.get('date', '—')}  市场：{data.get('market', '—')}",
                ]
                indicators = data.get("indicators", {})
                if indicators:
                    lines.append(f"指标 — CRI: {indicators.get('cri', '—')}  贪婪: {indicators.get('greedy', '—')}  乖离: {indicators.get('bias225Percentile', '—')}%  成本偏离: {indicators.get('costDeviationPercentile', '—')}%")
                    lines.append(f"         CRI分位: {indicators.get('criPercentile', '—')}%  贪婪分位: {indicators.get('greedyPercentile', '—')}%  MAHS: {indicators.get('mahs', '—')}  EMAHS: {indicators.get('emahs', '—')}")
                signals = data.get("signals", {})
                strict_signals = signals.get("strict", [])
                signal_type = signals.get("signalType", "")
                if strict_signals:
                    lines.append(f"信号：{' / '.join(strict_signals)}（类型: {signal_type}）")
                else:
                    lines.append(f"信号：无（类型: {signal_type or '—'}）")
                return "\n".join(lines)

            elif endpoint == "signals":
                if "error" in data:
                    return f"获取失败：{data['error']}"
                records = data.get("signals", [])
                lines = [f"最新扫描结果（{data.get('date', '—')}）共 {data.get('count', len(records))} 条："]
                for r in records[:20]:
                    lines.append(
                        f"  {r.get('code', '—')} {r.get('name', '—')} | "
                        f"{r.get('state', '—')} | 信号: {r.get('signals', '—')} | 类型: {r.get('type', '—')}"
                    )
                if len(records) > 20:
                    lines.append(f"  ... 等共 {len(records)} 条")
                return "\n".join(lines)

            elif endpoint == "watchlist":
                stocks = data.get("stocks", [])
                return f"股票池共 {data.get('count', len(stocks))} 只：{', '.join(stocks[:30])}{'...' if len(stocks) > 30 else ''}"

            elif endpoint == "scan":
                results = data.get("results", [])
                lines = [f"批量扫描结果（{data.get('count', len(results))} 只）："]
                for r in results:
                    if "error" in r:
                        lines.append(f"  {r.get('code', '—')} — 错误：{r['error']}")
                    else:
                        sigs = r.get("signals", {}).get("strict", [])
                        sig_type = r.get("signals", {}).get("signalType", "")
                        lines.append(
                            f"  {r.get('code', '—')} {r.get('name', '—')} | "
                            f"价格: {r.get('price', '—')} | "
                            f"信号: {'/'.join(sigs) if sigs else '无'} | 类型: {sig_type}"
                        )
                return "\n".join(lines)

            elif endpoint == "health":
                return f"peistock API 状态：{data.get('status', '—')}（{data.get('service', '—')}）"

            return json.dumps(data, ensure_ascii=False, indent=2)

        except urllib.error.URLError as e:
            return f"连接 peistock API 失败（{base_url}）：{e}. 请确认 peistock API 服务器已启动（npx tsx scripts/api-server.ts）"
        except Exception as e:
            logger.error(f"query_peistock 失败: {e}", exc_info=True)
            return f"查询失败：{e}"

    # ========== 分析层工具（DuckDB） ==========

    @tool(name="query_analytics", domain="admin", category="analytics", timeout=10, local_only=True)
    def query_analytics(self, sql: str) -> str:
        """执行 DuckDB 分析查询（仅限 SELECT，禁止修改数据）"""
        sql_lower = sql.strip().lower()
        forbidden = ["drop", "delete", "truncate", "insert", "update", "alter", "create", "replace"]
        if any(f in sql_lower for f in forbidden):
            return "错误：分析查询仅支持 SELECT，禁止修改数据"
        try:
            from mind.analytics import get_store
            store = get_store()
            rows = store.query(sql)
            return json.dumps(rows, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"query_analytics 失败: {e}")
            return f"查询失败: {e}"

    @tool(name="get_user_history", domain="admin", category="analytics", timeout=5, local_only=True)
    def get_user_history(self, user_id: str) -> str:
        """获取某用户最近的 Agent 执行历史摘要"""
        try:
            from mind.analytics import get_store
            store = get_store()
            rows = store.get_user_history(user_id)
            if not rows:
                return f"用户 {user_id} 暂无历史记录"
            return json.dumps(rows, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"get_user_history 失败: {e}")
            return f"查询失败: {e}"

    @tool(name="get_task_assets", domain="admin", category="analytics", timeout=5, local_only=True)
    def get_task_assets(self, user_id: str = None) -> str:
        """获取任务资产列表（报告、PDF 等）"""
        try:
            from mind.analytics import get_store
            store = get_store()
            rows = store.get_task_assets(user_id)
            if not rows:
                return "暂无任务资产"
            return json.dumps(rows, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"get_task_assets 失败: {e}")
            return f"查询失败: {e}"

    @tool(name="search_past_executions", domain="admin", category="analytics", timeout=5, local_only=True)
    def search_past_executions(self, query: str, user_id: str = None) -> str:
        """搜索历史执行记录（工具名、结果内容等）"""
        try:
            from mind.analytics import get_store
            store = get_store()
            rows = store.search_past_executions(query, user_id)
            if not rows:
                return f"未找到与「{query}」相关的历史记录"
            return json.dumps(rows, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"search_past_executions 失败: {e}")
            return f"查询失败: {e}"

    @tool(name="refresh_analytics", domain="admin", category="analytics", timeout=15, local_only=True)
    def refresh_analytics(self) -> str:
        """刷新分析数据（重新加载轨迹和任务资产）"""
        try:
            from mind.analytics import get_store
            store = get_store()
            return store.refresh()
        except Exception as e:
            logger.error(f"refresh_analytics 失败: {e}")
            return f"刷新失败: {e}"

    @tool(name="get_tool_stats", domain="admin", category="analytics", timeout=5, local_only=True)
    def get_tool_stats(self, days: int = 7) -> str:
        """获取最近 N 天的工具使用统计"""
        try:
            from mind.analytics import get_store
            store = get_store()
            rows = store.get_tool_stats(days)
            return json.dumps(rows, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"get_tool_stats 失败: {e}")
            return f"查询失败: {e}"

    @tool(name="save_learning", domain="admin", category="analytics", timeout=5, local_only=True)
    def save_learning(self, category: str, content: str, source_session: str = None, confidence: float = 0.8) -> str:
        """保存一条学习记录（失败教训、成功经验、用户偏好等）"""
        try:
            from mind.analytics import get_store
            store = get_store()
            return store.save_learning(category, content, source_session, confidence)
        except Exception as e:
            logger.error(f"save_learning 失败: {e}")
            return f"保存失败: {e}"

    @tool(name="get_learnings", domain="admin", category="analytics", timeout=5, local_only=True)
    def get_learnings(self, category: str = None, limit: int = 20) -> str:
        """获取已沉淀的学习记录（规则/偏好/失败/成功）"""
        try:
            from mind.analytics import get_store
            store = get_store()
            rows = store.get_learnings(category, limit)
            if not rows:
                return "暂无学习记录"
            return json.dumps(rows, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"get_learnings 失败: {e}")
            return f"查询失败: {e}"
