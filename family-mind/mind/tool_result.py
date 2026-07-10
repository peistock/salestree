"""
ToolResult 标准信封与 @tool 装饰器
规范：所有原子工具返回统一信封，含 care_signals 插槽
"""
import functools
import inspect
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToolResult:
    """原子工具标准输出信封"""

    def __init__(
        self,
        status: str = "success",
        result: Dict = None,
        latency_ms: int = 0,
        care_signals: List[Dict] = None,
        fallback_used: bool = False,
        error: str = None,
    ):
        self.status = status
        self.result = result or {}
        self.latency_ms = latency_ms
        self.care_signals = care_signals or []
        self.fallback_used = fallback_used
        self.error = error
        self._tool_name = "unknown"

    def to_dict(self) -> Dict:
        return {
            "status": self.status,
            "tool": self._tool_name,
            "latency_ms": self.latency_ms,
            "result": {
                **self.result,
                "care_signals": self.care_signals,
            },
            "audit": {
                "executed_by": "agent",
                "timestamp": datetime.now().isoformat(),
            },
            "fallback_used": self.fallback_used,
        }

    @property
    def value(self) -> Any:
        """快捷访问主返回值（兼容旧代码）"""
        return self.result.get("value", "")

    def __str__(self) -> str:
        if self.status == "error":
            return f"工具执行错误：{self.error}"
        return str(self.result.get("value", ""))


def tool(
    name: str = None,
    domain: str = "admin",
    category: str = "general",
    timeout: int = 30,
    fallback_on_timeout: bool = False,
    idempotent: bool = True,
    local_only: bool = False,
    care_scanner: str = "disabled",  # enabled | disabled
):
    """
    原子工具装饰器
    注册工具元数据，包装返回值，统一异常处理
    """

    def decorator(func):
        meta = {
            "name": name or func.__name__,
            "domain": domain,
            "category": category,
            "timeout": timeout,
            "fallback_on_timeout": fallback_on_timeout,
            "idempotent": idempotent,
            "local_only": local_only,
            "care_scanner": care_scanner,
        }

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                raw = func(*args, **kwargs)
                latency = int((time.time() - start) * 1000)

                # 如果返回已经是 ToolResult，直接补充元数据
                if isinstance(raw, ToolResult):
                    raw._tool_name = meta["name"]
                    raw.latency_ms = latency
                    return raw

                # 字符串/简单值 → 包装进 result.value
                if isinstance(raw, str):
                    result = ToolResult(
                        status="success",
                        result={"value": raw},
                        latency_ms=latency,
                    )
                elif isinstance(raw, dict):
                    result = ToolResult(
                        status="success",
                        result=raw,
                        latency_ms=latency,
                    )
                else:
                    result = ToolResult(
                        status="success",
                        result={"value": str(raw)},
                        latency_ms=latency,
                    )
                result._tool_name = meta["name"]
                return result

            except Exception as e:
                latency = int((time.time() - start) * 1000)
                logger.error(f"工具 {meta['name']} 执行异常: {e}")
                result = ToolResult(
                    status="error",
                    error=str(e),
                    latency_ms=latency,
                    fallback_used=True,
                )
                result._tool_name = meta["name"]
                return result

        wrapper._tool_meta = meta
        return wrapper

    return decorator


# ========== 参数类型强制转换（来自 Hermes）==========

def _coerce_value(value, expected_type):
    """把字符串值强制转换为 schema 声明的类型。"""
    if not isinstance(value, str):
        return value
    if isinstance(expected_type, list):
        for t in expected_type:
            result = _coerce_value(value, t)
            if result is not value:
                return result
        return value
    if expected_type in ("integer", "number"):
        try:
            f = float(value)
            if f == int(f):
                return int(f)
            return f
        except (ValueError, OverflowError):
            return value
    if expected_type == "boolean":
        low = value.strip().lower()
        if low == "true":
            return True
        if low == "false":
            return False
        return value
    return value


def coerce_tool_args(tool_name: str, args: dict, schema: dict) -> dict:
    """根据 JSON Schema 把字符串参数强制为正确类型。"""
    if not args or not isinstance(args, dict):
        return args
    properties = (schema.get("parameters") or {}).get("properties")
    if not properties:
        return args
    for key, value in list(args.items()):
        if not isinstance(value, str):
            continue
        prop_schema = properties.get(key)
        if not prop_schema:
            continue
        expected = prop_schema.get("type")
        if not expected:
            continue
        coerced = _coerce_value(value, expected)
        if coerced is not value:
            args[key] = coerced
    return args


# ========== 工具注册表 ==========

class ToolRegistry:
    """扫描并管理所有 @tool 标注的方法"""

    def __init__(self):
        self._tools: Dict[str, callable] = {}
        self._meta: Dict[str, Dict] = {}

    def register(self, method: callable):
        """注册一个带 @tool 装饰器的方法"""
        meta = getattr(method, "_tool_meta", None)
        if not meta:
            logger.warning(f"方法 {method.__name__} 没有 @tool 装饰器，跳过注册")
            return
        name = meta["name"]
        self._tools[name] = method
        self._meta[name] = meta
        logger.debug(f"注册工具: {name} (domain={meta['domain']})")

    def scan_instance(self, instance):
        """扫描实例上所有带 @tool 的方法并注册"""
        for attr_name in dir(instance):
            if attr_name.startswith("_"):
                continue
            method = getattr(instance, attr_name)
            if callable(method) and hasattr(method, "_tool_meta"):
                self.register(method)

    def execute(self, name: str, args: dict) -> ToolResult:
        """执行工具，返回标准信封"""
        method = self._tools.get(name)
        if not method:
            return ToolResult(
                status="error",
                error=f"未知工具: {name}",
                fallback_used=True,
            )
        if not isinstance(args, dict):
            err = f"调用工具 '{name}' 时参数必须是对象（dict），实际收到 {type(args).__name__}。"
            logger.warning(f"工具 {name} 参数类型错误: {type(args).__name__}")
            return ToolResult(status="error", error=err, fallback_used=True)

        # 参数类型强制转换
        schemas = self.schema()
        schema_map = {s["function"]["name"]: s["function"] for s in schemas}
        tool_schema = schema_map.get(name, {})
        args = coerce_tool_args(name, args, tool_schema)

        # 前置参数校验：1) schema required 字段  2) 值为 None 的检查  3) inspect 签名兜底
        missing = []
        schema_required = (tool_schema.get("parameters") or {}).get("required", [])
        for r in schema_required:
            if r not in args or args[r] is None:
                missing.append(r)

        # inspect 兜底：如果 schema 没标出 required，用函数签名补
        if not schema_required and method:
            try:
                sig = inspect.signature(method)
                for param_name, param in sig.parameters.items():
                    if param_name == "self":
                        continue
                    if param.default is inspect.Parameter.empty and param.kind in (
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY,
                    ):
                        if param_name not in args or args[param_name] is None:
                            missing.append(param_name)
            except (ValueError, TypeError):
                pass

        if missing:
            err = f"调用工具 '{name}' 时缺少必需参数：{', '.join(missing)}。请补全后重新调用。"
            logger.warning(f"工具 {name} 缺少参数: {missing}, args={args}")
            return ToolResult(
                status="error",
                error=err,
                fallback_used=True,
            )

        try:
            return method(**args)
        except TypeError as e:
            # 参数缺失或不匹配（兜底）
            err = str(e)
            logger.warning(f"工具 {name} 参数错误: {err}, args={args}")
            return ToolResult(
                status="error",
                error=f"调用工具 '{name}' 时参数不对：{err}。请检查必需参数是否都已提供，并重新调用。",
                fallback_used=True,
            )

    def get_meta(self, name: str) -> Optional[Dict]:
        return self._meta.get(name)

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())

    def schema(self) -> List[Dict]:
        """返回 OpenAI Function Calling 格式的工具定义"""
        schema_map = {
            "read_file": {
                "description": "读取本地文件内容",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string", "description": "相对路径或绝对路径"}
                }, "required": ["path"]}
            },
            "write_file": {
                "description": "写入文件（仅限工作目录内）。必须同时提供 path（文件相对路径）和 content（文件内容）。长报告建议分段追加写：先 mode='w' 写开头，再多次 mode='a' 追加后续章节，避免单次内容过大。",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string", "description": "文件相对路径，如 report.md、data/analysis.txt"},
                    "content": {"type": "string", "description": "要写入的文件内容（mode=w时为完整内容，mode=a时为追加片段）"},
                    "mode": {"type": "string", "enum": ["w", "a"], "default": "w", "description": "写入模式：w=覆盖写入（默认），a=追加到文件末尾"}
                }, "required": ["path", "content"]}
            },
            "search_knowledge": {
                "description": "语义搜索销售知识库（方案、客户资料、行业文档）。理解自然语言，不依赖关键词精确匹配。",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string", "description": "自然语言查询"}
                }, "required": ["query"]}
            },
            "list_dir": {
                "description": "列出目录文件",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string", "default": "."}
                }}
            },
            "get_time": {
                "description": "获取当前时间",
                "parameters": {"type": "object", "properties": {}}
            },
            "search_web": {
                "description": "联网搜索实时信息（新闻、热点、公开资料、政策）。当用户问'今天发生了什么''最近有什么新闻''查一下xxx'等时效性问题时调用。底层为本地 SearXNG 聚合搜索。",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "default": 5, "description": "返回结果数量"}
                }, "required": ["query"]}
            },
            "fetch_webpage": {
                "description": "直接 HTTP 获取网页内容（不经过浏览器渲染）。适合文章、博客、公告等静态页面，比浏览器更快更省 token。",
                "parameters": {"type": "object", "properties": {
                    "url": {"type": "string", "description": "网页 URL，必须完整（含 https://）"}
                }, "required": ["url"]}
            },
            "jina_reader": {
                "description": "用 Jina AI Reader 将网页转为 Markdown，大幅节省 token。适合文章、博客、文档。限 20 RPM。",
                "parameters": {"type": "object", "properties": {
                    "url": {"type": "string", "description": "网页 URL，必须完整（含 https://）"}
                }, "required": ["url"]}
            },
            "find_chrome_url": {
                "description": "从本地 Chrome 书签/历史中检索 URL。用于定位之前访问过的页面或内部系统（公网搜不到的目标）。",
                "parameters": {"type": "object", "properties": {
                    "keywords": {"type": "string", "description": "搜索关键词，空格分隔多词 AND"},
                    "limit": {"type": "integer", "default": 10, "description": "返回条数上限"},
                    "since": {"type": "string", "description": "时间窗如 7d/24h/YYYY-MM-DD（仅历史）"}
                }, "required": ["keywords"]}
            },
            "browse_open": {
                "description": "用真实浏览器打开网页，返回标题和文字内容摘要。适合动态渲染页面、需要登录态、需要交互操作的场景。",
                "parameters": {"type": "object", "properties": {
                    "url": {"type": "string", "description": "网页 URL，必须完整（含 https://）"}
                }, "required": ["url"]}
            },
            "browse_click": {
                "description": "在已打开的网页中点击某个元素（按钮、链接等）。",
                "parameters": {"type": "object", "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器"}
                }, "required": ["selector"]}
            },
            "browse_fill": {
                "description": "在已打开的网页输入框中填写文字。",
                "parameters": {"type": "object", "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"}
                }, "required": ["selector", "text"]}
            },
            "browse_screenshot": {
                "description": "对当前网页截图保存到工作目录。",
                "parameters": {"type": "object", "properties": {
                    "filename": {"type": "string", "description": "截图文件名"}
                }}
            },
            "browse_scroll": {
                "description": "滚动当前网页。",
                "parameters": {"type": "object", "properties": {
                    "direction": {"type": "string", "description": "down/up/top/bottom", "default": "down"}
                }}
            },
            "browse_text": {
                "description": "提取当前网页指定区域的文字内容。",
                "parameters": {"type": "object", "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器，默认 body", "default": "body"}
                }}
            },
            "execute_code": {
                "description": "执行 Python 代码，适合数据处理、文件生成、格式转换。禁止 rm/chmod/system/subprocess/eval/exec，超时30秒。",
                "parameters": {"type": "object", "properties": {
                    "code": {"type": "string", "description": "Python 代码"},
                    "language": {"type": "string", "default": "python"}
                }, "required": ["code"]}
            },
            "pip_install": {
                "description": "安装 PyPI 包。禁止安装系统/网络相关包。",
                "parameters": {"type": "object", "properties": {
                    "package": {"type": "string"},
                    "packages": {"type": "array", "items": {"type": "string"}}
                }}
            },
            "install_skill": {
                "description": "从 URL 或文本安装新 skill",
                "parameters": {"type": "object", "properties": {
                    "source": {"type": "string", "description": "URL 或 skill 文本"},
                    "skill_name": {"type": "string", "description": "可选，指定 skill 文件名"}
                }, "required": ["source"]}
            },
            "list_skills": {
                "description": "列出已安装的所有 skills",
                "parameters": {"type": "object", "properties": {}}
            },
            "set_reminder": {
                "description": "设置一次性提醒。时间支持 '18:00'、'2026-04-30 18:00' 等格式。",
                "parameters": {"type": "object", "properties": {
                    "user_id": {"type": "string"},
                    "content": {"type": "string"},
                    "remind_time": {"type": "string"}
                }, "required": ["user_id", "content", "remind_time"]}
            },
            "todo": {
                "description": "管理当前会话的任务列表。用于复杂多阶段任务（如深度研究、写报告、生成PDF等）分解步骤、跟踪进度。调用时不传参数可读取当前列表。",
                "parameters": {"type": "object", "properties": {
                    "todos": {
                        "type": "array",
                        "description": "任务项数组，不传则读取当前列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "唯一标识"},
                                "content": {"type": "string", "description": "任务描述"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]}
                            },
                            "required": ["id", "content", "status"]
                        }
                    },
                    "merge": {"type": "boolean", "description": "true=按id更新，false=替换全部", "default": False}
                }}
            },
            "plan": {
                "description": "制定或更新结构化执行计划。适用于涉及3个以上步骤的复杂任务（深度研究、分析报告、多源数据整合等）。制定计划后系统会自动跟踪进度并生成可视化反馈。可以在执行过程中随时调用更新计划（如增加、删除、调整步骤）。",
                "parameters": {"type": "object", "properties": {
                    "steps": {
                        "type": "array",
                        "description": "执行步骤列表，每项包含步骤编号、描述和预计使用的工具",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "步骤编号，如 \"1\", \"2a\""},
                                "description": {"type": "string", "description": "步骤描述，一句话说明要做什么"},
                                "expected_tools": {"type": "array", "items": {"type": "string"}, "description": "可选，预计使用的工具列表，如 [\"search_web\", \"fetch_webpage\"]"}
                            },
                            "required": ["id", "description"]
                        }
                    },
                    "reason": {"type": "string", "description": "制定计划的原因，如\"这是一个多阶段深度分析任务\"", "default": ""}
                }, "required": ["steps"]}
            },
            "delegate": {
                "description": "将复杂任务拆分为多个子任务并行执行。每个子任务由一个独立的子Agent完成，适合需要同时从多个角度收集信息的场景（如并行搜索、并行分析）。",
                "parameters": {"type": "object", "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "子任务列表，每个子任务包含目标和可选背景",
                        "items": {
                            "type": "object",
                            "properties": {
                                "goal": {"type": "string", "description": "子任务目标，描述清楚要做什么"},
                                "context": {"type": "string", "description": "可选的背景信息"}
                            },
                            "required": ["goal"]
                        }
                    }
                }, "required": ["tasks"]}
            },
            "md_to_pdf": {
                "description": "将工作目录中的 Markdown 文件转换为 PDF 报告。自动处理中文字体、标题层级、列表和表格。输出文件与原文件同名，扩展名改为 .pdf。",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string", "description": "工作目录内的 Markdown 文件相对路径（如 report.md）"},
                    "title": {"type": "string", "description": "PDF 封面标题，默认从文件内容提取"}
                }, "required": ["path"]}
            },
            "query_peistock": {
                "description": "查询 peistock 股票数据（价格、指标、B/S信号）。必须传入 code 参数（如 600519、00700）。",
                "parameters": {"type": "object", "properties": {
                    "endpoint": {"type": "string", "description": "接口类型：stock（单股查询，默认）| signals（最新信号）| watchlist（股票池）| scan（批量扫描）| health（服务健康）", "default": "stock"},
                    "code": {"type": "string", "description": "股票代码（如 600519、00700）。endpoint=stock 时必填"},
                    "codes": {"type": "array", "items": {"type": "string"}, "description": "股票代码列表（endpoint=scan 时使用）"}
                }, "required": ["code"]}
            },
            "multi_search": {
                "description": "并行执行多个搜索查询（Intent Analysis 多子查询）。适合复杂问题需要拆成多个角度同时搜索。",
                "parameters": {"type": "object", "properties": {
                    "queries": {"type": "array", "items": {"type": "string"}, "description": "搜索查询列表，如 ['鄂尔多斯5月天气', '鄂尔多斯带娃旅游注意事项']"},
                    "max_results": {"type": "integer", "description": "每个查询返回结果数", "default": 3}
                }, "required": ["queries"]}
            },
            "spawn_task": {
                "description": "创建子任务并由 Coordinator 自动调度执行。当复杂任务需要拆分为多个子任务并行执行时使用（如并行搜索不同角度、并行分析多个数据源）。每个子任务有独立的 work_dir、todo_store、plan_store 和 checkpoint。",
                "parameters": {"type": "object", "properties": {
                    "goal": {"type": "string", "description": "子任务目标，描述清楚要做什么"},
                    "task_type": {"type": "string", "description": "任务类型：research（研究）/ analysis（分析）/ writing（写作）/ coding（代码）/ verification（验证）/ composite（复合）", "default": "research"},
                    "dependencies": {"type": "array", "items": {"type": "string"}, "description": "依赖的任务 ID 列表，这些任务完成后本子任务才会启动"}
                }, "required": ["goal"]}
            },
            "get_task_status": {
                "description": "查询指定任务的状态、结果摘要、子任务数量和整体进度。",
                "parameters": {"type": "object", "properties": {
                    "task_id": {"type": "string", "description": "要查询的任务 ID"}
                }, "required": ["task_id"]}
            },
            "cancel_task": {
                "description": "取消指定任务（级联取消其子任务）。",
                "parameters": {"type": "object", "properties": {
                    "task_id": {"type": "string", "description": "要取消的任务 ID"}
                }, "required": ["task_id"]}
            },
        }
        schemas = []
        for name, meta in self._meta.items():
            base = schema_map.get(name, {})
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": base.get("description", f"{name} ({meta['domain']})"),
                    "parameters": base.get("parameters", {"type": "object", "properties": {}}),
                }
            })
        # 注入非 @tool 注册的特殊工具（如 todo、plan、coordinator tools）
        for special in ("todo", "plan", "spawn_task", "get_task_status", "cancel_task"):
            if special in schema_map:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": special,
                        "description": schema_map[special]["description"],
                        "parameters": schema_map[special]["parameters"],
                    }
                })
        return schemas
