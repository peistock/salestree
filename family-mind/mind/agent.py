"""
Agent 执行引擎（Phase 2 升级版 + 国内模型适配）
Plan -> Act -> Observe -> Reflect -> Consolidate
新增：向量检索前置、中期记忆整合、国内模型通用适配
"""
import os
import json
import threading
import time
import logging
from pathlib import Path
from typing import Optional, List, Dict

from mind.memory import Memory
from mind.tools import Toolkit
from mind.llm_client import LLMClient, chat
from mind.association_engine import build_association_context
from mind.interruption import is_interruption, suspend, resume, has_suspended
from mind.emotion_sensor import process_message as process_emotion
from mind.todo_store import TodoStore, todo_tool
from mind.plan_store import PlanStore
from mind.agent_loop import AgentLoop
from mind.agent_message import AgentMessage
from mind.agent_trace import AgentTraceStore
from mind.services import AgentServices
from mind.coordinator import TaskCoordinator
from mind.task_engine import TaskType

logger = logging.getLogger(__name__)

# 模型选择（从环境变量读取，默认 DeepSeek）
MODEL_DAILY = os.getenv("MODEL_DAILY", "qwen3.6-plus")
MODEL_COMPLEX = os.getenv("MODEL_COMPLEX", "qwen3.6-plus")

# Skill 触发词映射 → skill 文件名（不含 .md）
SKILL_TRIGGERS = {
    "account-research": [
        "客户研究", "查这个公司", "这家公司", "客户背景", "account research",
        "battlecard", "公司背景", "客户公司", "纵横分析", "横纵分析",
    ],
    "account-marketing": [
        "营销触点", "品牌调研", "投放分析", "营销打法", "社媒分析",
        "内容策略", "KOL 投放", "广告分析", "marketing touchpoints",
        "这家公司怎么营销", "营销渠道", "营销调研",
    ],
    "outreach-drafter": [
        "写邮件", "写跟进", "触达", "写话术", "cold call", "跟进邮件",
        "微信跟进", "邮件跟进", "跟进文案", "发邮件给", "写封邮件",
    ],
    "sales-coach": [
        "销售教练", "话术练习", "模拟拜访", "模拟电话", "role play", "演练",
        "陪练", "异议处理", "客户刁钻", "怎么回", "你来当客户", "你当客户",
        "我当销售", "你当销售", "示范一下", "拜访演练", "电话演练",
    ],
    "marketing-plan": [
        "营销方案", "营销策划", "营销方向", "营销计划", "marketing plan",
        "出一版方案", "写个策划案", "品牌方案", "传播方案", "增长方案",
        "营销提案", "整合营销", "行业动态及营销方向",
    ],
    "news-digest": [
        "行业资讯", "资讯", "news digest", "给客户发资讯", "分享行业",
        "每周资讯", "行业早报", "行业周报", "资讯摘要", "行业洞察",
    ],
    "deep-analysis": [
        # 深度版明确触发词（优先匹配，避免被 hv-analysis 截胡）
        "深度分析", "完整版", "深度版", "详细版", "万字", "长报告",
        "完整报告", "详细分析", "全面分析", "详细研究", "深度了解",
        "深入研究", "深入分析", "一万字", "几万字", "长篇",
        " thorough", " in-depth",
    ],
    "hv-analysis": [
        # 书面/专业说法（常规分析触发）
        "研究", "分析", "调研", "deep research", "竞品分析",
        # 老人口语："是怎么回事"
        "怎么回事", "是怎么回事", "是什么来头", "什么背景",
        # 老人口语：想了解全貌
        "了解一下", "给我讲讲", "给我说说", "帮我讲讲", "帮我介绍",
        # 老人口语：摸底/查清楚
        "帮我查查", "帮我摸清楚", "帮我搞清楚", "帮我弄明白", "帮我摸透",
        # 老人口语：看看这个怎么样
        "帮我看看", "帮我看看这个", "怎么样", "好不好", "靠不靠谱",
        # 老人口语：常规分析修饰词
        "整体情况",
    ],
    "khazix-writer": ["写文章", "写稿", "公众号", "续写", "扩写", "出稿", "按我的风格写", "帮我写成文章"],
    "neat-freak": ["整理", "同步", "收尾", "梳理", "更新文档", "这个阶段做完了", "新人能直接上手", "文档对齐"],
    "web-access": ["搜索", "查一下", "上网", "网页", "抓取", "爬取", "看看这个链接", "打开网页"],
    "nuwa-skill": ["造skill", "蒸馏", "女娲", "造人", "思维方式", "视角", "思维框架"],
    "ppt-master": ["做PPT", "幻灯片", "演示文稿", "deck", "presentation"],
    "peter-lynch-perspective": ["投资分析", "选股", "股票", "财报分析", "林奇"],
    "laotalk-perspective": ["产品分析", "运营分析", "互联网产品", "用户增长", "商业化"],
    "self-improving": ["自进化", "自我改进", "优化自己", "复盘", "总结改进"],
    "news-aggregator-skill": ["新闻", "资讯", "热点", "今天发生了什么", "最新动态"],
    "stock-announcement-analysis": ["公告", "研报", "上市公司公告", "财报", "业绩"],
    "install-skill": ["安装技能", "装skill", "添加技能", "新技能", "给我装个", "能不能装"],
}


class AgentSession:
    """Agent 会话状态层 — 一次用户交互的完整生命周期

    职责：管理单次交互的状态（消息历史、任务目录、待办清单、checkpoint）。
    基础设施（LLM、全局配置）通过 AgentServices 注入，实现 Services/Session 分离。
    """

    # Checkpoint 过期时间（秒）
    CHECKPOINT_TTL = 7200  # 2 小时

    def __init__(self, user_id: str, user_name: str, services: AgentServices = None):
        self.user_id = user_id
        self.user_name = user_name
        self.services = services or AgentServices.default()
        self.memory = Memory(user_id, user_name)
        self.work_dir = Path(os.getenv("DATA_DIR", "./data")) / "tasks" / f"{user_id}_{int(time.time())}"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.tools = Toolkit(self.work_dir)
        self.todo_store = TodoStore()
        self.plan_store = PlanStore(self.todo_store)
        self._checkpoint_path = Path(os.getenv("DATA_DIR", "./data")) / "checkpoints" / f"{user_id}.json"
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def llm(self):
        """向后兼容：通过 services 访问 LLM"""
        return self.services.llm

    def run(self, query: str, event_sink=None, cancel_event: Optional[threading.Event] = None) -> dict:
        """执行完整 Agent 循环（Tool Calling 架构 — Hermes while 循环适配版）
        event_sink: 标准事件流回调，接收 AgentEvent 对象
        cancel_event: 外部取消信号，设置后 Agent 循环会尽快退出
        返回: {"reply": str, "mode": int}  mode: 1=单次直回, 4=结果分段
        """
        if not self.llm.is_ready():
            return {"reply": "销销还没配置好，请联系管理员设置 LLM_API_KEY。", "mode": 1}

        thread_id = self.memory.get_or_create_thread(self.user_id)

        # ===== 快速路由：确定性查询直接走工具，不经过 LLM 编故事 =====
        q = query.lower()
        if ("技能" in q and ("哪些" in q or "会什么" in q or "有什么" in q or "有啥" in q or "装了" in q)) or \
           ("你会" in query and "什么" in query) or \
           ("你能" in query and ("做什么" in q or "干什么" in q)) or \
           ("本领" in q or "本事" in q) and ("什么" in q or "哪些" in q or "有啥" in q):
            reply = str(self.tools.execute("list_skills", {}))
            self._save_exchange(thread_id, query, reply)
            self.memory.close()
            return {"reply": reply, "mode": 1}

        if "几点" in query or "时间" in query or ("现在" in q and "几点" in q):
            reply = str(self.tools.execute("get_time", {}))
            self._save_exchange(thread_id, query, reply)
            self.memory.close()
            return {"reply": reply, "mode": 1}

        # ===== 打断检测 =====
        if has_suspended(self.user_id) and is_interruption(self.user_id, query):
            pass

        mem = self.memory.load()

        # ===== Phase 4：Briefing + Override 注入 =====
        from datetime import date
        from mind.memory import get_active_briefings, get_pending_overrides, mark_override_applied
        briefings = get_active_briefings(self.user_id, date.today())
        if briefings:
            briefing_text = "\n".join(f"- {b['content']}" for b in briefings)
            mem["l1_core"] += f"\n\n[今日托付]\n{briefing_text}"
            logger.info(f"Briefing 已注入: user={self.user_id}, count={len(briefings)}")
        overrides = get_pending_overrides(self.user_id)
        if overrides:
            top = overrides[0]
            priority_label = {3: "【立即】", 2: "【紧急】", 1: "【注意】"}.get(top["priority"], "【注意】")
            mem["l1_core"] += f"\n\n[紧急指令]{priority_label}\n{top['content']}"
            mark_override_applied(top["id"])
            logger.info(f"Override 已注入并标记应用: id={top['id']}, priority={top['priority']}")

        # ===== Skill 按需加载 =====
        matched_skill = self._match_skill(query)
        # 如果用户查询没匹配到skill，但todo状态显示正在进行深度报告，自动加载deep-analysis
        if not matched_skill:
            matched_skill = self._match_skill_by_todo_state()
        if matched_skill:
            self._save_last_skill(matched_skill)
            skill_content = self.memory.load_skill(matched_skill, max_chars=0)
            if skill_content:
                # 改动1：Skill 外置化 — 长 skill 不注入全文，提示模型按需读取
                if len(skill_content) > 3000:
                    skill_path = os.path.abspath(os.path.join(os.getenv("DATA_DIR", "./data"), "skills", f"{matched_skill}.md"))
                    mem["l4_skills"] = f"【Skill 可用】当前加载了 '{matched_skill}' skill。如需完整指导，请用 read_file 工具读取 {skill_path}\n\n" + mem["l4_skills"]
                    logger.info(f"Skill 已外置化: {matched_skill} ({len(skill_content)} 字符)")
                else:
                    mem["l4_skills"] = skill_content + "\n\n" + mem["l4_skills"]
                    logger.info(f"Skill 已加载: {matched_skill}")

        # ===== 关联引擎 =====
        association_ctx = build_association_context(self.user_id, query, self.user_name)
        if association_ctx:
            mem["l3_episodes"] = association_ctx + "\n\n" + mem["l3_episodes"]

        # ===== 知识库前置检索 =====
        knowledge_context = self._retrieve_knowledge(query)
        if knowledge_context:
            mem["l4_skills"] = knowledge_context + "\n\n" + mem["l4_skills"]

        # ===== 销销树资讯工作台（外脑）自动检索 =====
        # 当用户聊到客户/行业/竞品/融资等销售信号时，自动从 wechat-digest KB 召回相关文章
        account_news_context = self._retrieve_account_news(query)
        if account_news_context:
            mem["l4_skills"] = account_news_context + "\n\n" + mem["l4_skills"]

        # ===== 构建 system prompt =====
        system = self._build_system_prompt(mem)

        # ===== 改动2：System Prompt 分层 + 改动4：记忆按需加载 =====
        # 记忆内容从 system prompt 中移除，改为 assistant 消息注入，避免 system 过长
        # 近期对话历史已作为独立 user/assistant 消息对注入，此处不再重复放入 l3_episodes 摘要
        memory_parts = []
        for key, label, max_len in [
            ("l1_core", "核心定位", 800),
            ("l2_profile", "用户画像", 1200),
            ("l3_summaries", "历史摘要", 400),
            ("l3_team_shared", "团队共享", 400),
            ("l3_workspace", "工作空间", 400),
            ("l4_skills", "技能知识", 2000),
        ]:
            text = mem.get(key, "")
            if text and text.strip() and text.strip() != "（无）":
                if len(text) > max_len:
                    text = text[:max_len] + "..."
                memory_parts.append(f"【{label}】\n{text}")
        memory_context = "\n\n".join(memory_parts)

        # ===== Tool Calling while 循环（Hermes 模式适配）=====
        # 记忆上下文和 query 分两条消息，避免 IntentRouter 误判（只看最后一条 user）
        messages: list[AgentMessage] = [AgentMessage.system(system)]
        if memory_context:
            messages.append(AgentMessage.user(f"【背景信息】\n{memory_context}"))
            messages.append(AgentMessage.assistant("收到，我会结合这些背景信息来回答。"))

        # 注入当前线程近期对话历史（实际 user/assistant 消息对），避免每次发消息都失忆
        try:
            recent_pairs = self.memory.load_recent_messages(thread_id, limit=20)
            # 当前 query 尚未保存，但防御性过滤
            recent_pairs = [(r, c) for r, c in recent_pairs if not (r == "user" and c == query)]
            total_chars = 0
            max_history_chars = 12000
            per_msg_max = 2000
            for role, content in recent_pairs:
                if total_chars >= max_history_chars:
                    break
                if len(content) > per_msg_max:
                    content = content[:per_msg_max] + "\n...[内容过长，已截断]"
                messages.append(AgentMessage(role=role, content=content))
                total_chars += len(content)
            if recent_pairs:
                logger.info(f"已注入近期对话历史: user={self.user_id}, pairs={len(recent_pairs)}, chars={total_chars}")
        except Exception as e:
            logger.warning(f"加载近期对话历史失败: {e}")

        messages.append(AgentMessage.user(f"用户「{self.user_name}」说：{query}"))

        tools = self.tools.schema()
        care_signals = []
        final_reply = ""
        max_iterations = 50
        start_iteration = 0

        # ===== Checkpoint 恢复 =====
        if self._is_resume_query(query):
            checkpoint = self._load_checkpoint()
            if checkpoint:
                recovered = self._restore_from_checkpoint(checkpoint, system, query)
                if recovered:
                    messages = recovered["messages"]
                    self.todo_store = recovered["todo_store"]
                    start_iteration = recovered["iteration"]
                    logger.info(f"Checkpoint 恢复成功: user={self.user_id}, iteration={start_iteration}")

        # 挂起当前任务（用于后续打断恢复）
        suspend(
            self.user_id,
            query,
            messages=[m.to_llm() for m in messages if m.role != "system"],
            todos=self.todo_store.read(),
            iteration=start_iteration,
            work_dir=str(self.work_dir),
        )

        # ===== Coordinator 模式分支 =====
        use_coordinator = self._should_use_coordinator(query, matched_skill)
        if use_coordinator and start_iteration == 0:
            # 复杂任务使用 Coordinator 模式（根任务顺序执行，子任务并发）
            coord_result = self._run_with_coordinator(
                query=query,
                system=system,
                messages=messages,
                event_sink=event_sink,
                thread_id=thread_id,
            )
            final_reply = coord_result["reply"]
            generated_files = coord_result.get("files", [])

            # 记录用户最新任务目录，供 Web 任务面板展示
            coord_work_dir = coord_result.get("work_dir")
            if coord_work_dir:
                self._record_latest_task(Path(coord_work_dir))

            # 清理打断状态（Coordinator 分支也需要）
            from mind.interruption import clear
            clear(self.user_id)

            # 保存对话、情绪检测
            self._save_exchange(thread_id, query, final_reply)
            process_emotion(self.user_id, query, self.user_name)
            self.memory.close()

            mode = 4 if len(final_reply) > 800 else 1
            return {"reply": final_reply, "mode": mode, "files": generated_files}

        # ===== 标准模式：单 AgentLoop =====
        trace_store = AgentTraceStore(self.user_id)
        loop = AgentLoop(
            llm=self.llm,
            toolkit=self.tools,
            todo_store=self.todo_store,
            work_dir=self.work_dir,
            event_sink=event_sink,
            trace_store=trace_store,
            plan_store=self.plan_store,
        )

        try:
            result = loop.run(
                messages=messages,
                max_iterations=50,
                start_iteration=start_iteration,
                checkpoint_path=self._checkpoint_path,
                cancel_event=cancel_event,
            )
            final_reply = result["reply"]
            care_signals = result["care_signals"]

        except Exception as e:
            logger.error(f"Agent 循环异常: {e}", exc_info=True)
            final_reply = "销销出了点问题，请您稍后再试，或者联系管理员帮忙看看。"
            care_signals = []

        finally:
            from mind.interruption import clear
            clear(self.user_id)

            # 任务成功完成 → 清理 checkpoint
            should_cleanup = (
                final_reply
                and not final_reply.startswith("销销出了点问题")
                and not final_reply.startswith("销销正在努力")
            )
            if should_cleanup:
                try:
                    if self._checkpoint_path.exists():
                        self._checkpoint_path.unlink()
                        logger.info(f"Checkpoint 已清理: {self.user_id}")
                except Exception:
                    pass

            # 打断恢复
            follow_up = resume(self.user_id, query, final_reply)
            if follow_up:
                final_reply += "\n\n" + follow_up

            # 保存对话、情绪检测、care_signals
            self._save_exchange(thread_id, query, final_reply)
            process_emotion(self.user_id, query, self.user_name)
            if care_signals:
                from mind.care_scanner import notify_couple_from_signals
                notify_couple_from_signals(self.user_id, self.user_name, care_signals)

            self.memory.close()

        # 扫描工作目录中生成的文件（PDF、图片等）
        generated_files = []
        try:
            for p in self.work_dir.rglob("*"):
                if p.is_file() and p.stat().st_size > 100:
                    # 排除脚本和中间文件，只推送最终成果
                    suffix = p.suffix.lower()
                    if suffix in (".pdf", ".png", ".jpg", ".jpeg", ".mp3", ".mp4", ".docx", ".pptx"):
                        generated_files.append(str(p))
        except Exception:
            pass

        # 记录用户最新任务目录
        self._record_latest_task(self.work_dir)

        # 判断回复模式
        mode = 4 if len(final_reply) > 800 else 1
        return {"reply": final_reply, "mode": mode, "files": generated_files}



    # ========== Checkpoint 断点续作 ==========

    def _is_resume_query(self, query: str) -> bool:
        """判断用户是否想继续之前的任务。"""
        resume_keywords = ["继续", "接着做", "resume", "接着来", "继续刚才", "接着刚才", "resume task", "continue"]
        q = query.lower()
        return any(k in q for k in resume_keywords)


    def _load_checkpoint(self) -> Optional[dict]:
        """加载 checkpoint，如果过期或不存在返回 None。"""
        if not self._checkpoint_path.exists():
            return None
        try:
            data = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) > self.CHECKPOINT_TTL:
                logger.info("Checkpoint 已过期，忽略")
                self._checkpoint_path.unlink(missing_ok=True)
                return None
            return data
        except Exception as e:
            logger.warning(f"加载 checkpoint 失败: {e}")
            return None

    def _restore_from_checkpoint(self, checkpoint: dict, new_system: str, new_query: str) -> Optional[dict]:
        """从 checkpoint 恢复状态，返回恢复后的状态字典。"""
        try:
            from mind.todo_store import TodoStore
            restored_messages: list[AgentMessage] = [
                AgentMessage.system(new_system),
            ]
            for m in checkpoint.get("messages", []):
                restored_messages.append(AgentMessage.from_llm(m))
            # 追加恢复提示
            restored_messages.append(AgentMessage.user(
                f"用户「{self.user_name}」说：{new_query}（注意：这是从中断处恢复的任务，请继续之前的工作，不要重复已完成的内容）"
            ))

            todo_store = TodoStore()
            restored_todos = []
            for item in checkpoint.get("todos", []):
                restored_todos.append({
                    "id": item.get("id", ""),
                    "content": item.get("content", ""),
                    "status": item.get("status", "pending"),
                })
            if restored_todos:
                todo_store.write(restored_todos, merge=False)

            return {
                "messages": restored_messages,
                "todo_store": todo_store,
                "iteration": checkpoint.get("iteration", 0),
            }
        except Exception as e:
            logger.error(f"恢复 checkpoint 失败: {e}")
            return None

    @staticmethod
    def _sanitize_messages(messages: list) -> list:
        """清理消息列表中的 orphaned tool_call / tool_result 配对。

        每轮 API 调用前必做，防止：
        1. tool result 没有对应的 assistant tool_call → API 400
        2. assistant tool_call 没有对应的 tool result → API 400
        """
        # 1. 收集所有存在的 tool_call_id
        surviving_call_ids = set()
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    cid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if cid:
                        surviving_call_ids.add(cid)

        result_call_ids = set()
        for msg in messages:
            if msg.get("role") == "tool":
                cid = msg.get("tool_call_id")
                if cid:
                    result_call_ids.add(cid)

        # 移除 orphaned tool results（没有对应 assistant tool_call 的）
        orphaned = result_call_ids - surviving_call_ids
        if orphaned:
            messages = [
                m for m in messages
                if not (m.get("role") == "tool" and m.get("tool_call_id") in orphaned)
            ]

        # 为缺少 result 的 tool_call 插入 stub
        missing = surviving_call_ids - result_call_ids
        if missing:
            patched = []
            for msg in messages:
                patched.append(msg)
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        cid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                        if cid in missing:
                            patched.append({
                                "role": "tool",
                                "content": "[结果缺失 — 参见上下文摘要]",
                                "tool_call_id": cid,
                            })
            messages = patched

        return messages

    def _determine_mode(self, plan: dict) -> int:
        """判断回复模式：1=单次直回, 2=收到+结果, 3=进度更新, 4=结果分段"""
        if plan.get("type") == "simple":
            return 1
        steps = plan.get("steps", [])
        if len(steps) <= 3:
            return 2
        return 3

    def _match_skill(self, query: str) -> str:
        """根据用户查询匹配 skill 名称，返回 skill 文件名（不含 .md）或空字符串"""
        # 修复 #12：带"继续"的查询优先恢复之前的 skill，而不是被"深度分析"等词重新路由
        resumed_skill = self._get_resumed_skill(query)
        if resumed_skill:
            logger.info(f"继续意图命中，复用上一个 skill: {resumed_skill}")
            return resumed_skill

        for skill_name, triggers in SKILL_TRIGGERS.items():
            for trigger in triggers:
                if trigger in query:
                    return skill_name
        return ""

    def _load_checkpoint_light(self) -> Optional[dict]:
        """轻量读取 checkpoint，用于继续意图判断。"""
        if not self._checkpoint_path.exists():
            return None
        try:
            data = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) > self.CHECKPOINT_TTL:
                return None
            return data
        except Exception as e:
            logger.warning(f"读取 checkpoint 失败: {e}")
            return None

    def _get_resumed_skill(self, query: str) -> Optional[str]:
        """如果用户想继续之前的任务，返回应该沿用的 skill。"""
        resume_keywords = ["继续", "接着做", "resume", "接着来", "继续刚才", "接着刚才", "continue"]
        q = query.lower()
        if not any(k in q for k in resume_keywords):
            return None

        # 优先根据 checkpoint 中的 todo 推断原 skill
        checkpoint = self._load_checkpoint_light()
        if checkpoint:
            for item in checkpoint.get("todos", []):
                content = item.get("content", "")
                if "阶段" in content and "深度" not in content:
                    return "hv-analysis"
                if any(k in content for k in ["深度", "万字", "完整报告"]):
                    return "deep-analysis"

        # 否则复用最近一次 skill
        return self._load_last_skill()

    @property
    def _last_skill_path(self) -> Path:
        return Path(os.getenv("DATA_DIR", "./data")) / "state" / f"{self.user_id}_last_skill.json"

    def _load_last_skill(self) -> Optional[str]:
        """读取用户最近一次使用的 skill（2小时内有效）。"""
        p = self._last_skill_path
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) > self.CHECKPOINT_TTL:
                return None
            return data.get("skill")
        except Exception as e:
            logger.warning(f"读取 last_skill 失败: {e}")
            return None

    def _save_last_skill(self, skill: str):
        """保存用户最近一次使用的 skill。"""
        if not skill:
            return
        p = self._last_skill_path
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(
                json.dumps({"skill": skill, "timestamp": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"保存 last_skill 失败: {e}")

    def _match_skill_by_todo_state(self) -> str:
        """基于 todo 状态推断应该加载哪个 skill。用户查询未匹配时使用。"""
        todos = self.todo_store.read()
        if not todos:
            return ""
        for item in todos:
            content = item.get("content", "")
            status = item.get("status", "")
            if status in ("in_progress", "pending"):
                # 深度报告相关任务 → 加载 deep-analysis
                if any(k in content for k in ["深度", "完整报告", "万字", "深度分析", "深度研究"]):
                    logger.info(f"基于todo状态自动加载deep-analysis: {content}")
                    return "deep-analysis"
                # hv-analysis 的阶段4相关
                if any(k in content for k in ["阶段4", "完整版", "生成报告", "PDF"]):
                    # 如果 todo 里有阶段4任务但没明确说深度/常规，检查是否有已完成的深度分析前置步骤
                    for t in todos:
                        if any(k in t.get("content", "") for k in ["深度", "万字", "深度分析"]):
                            return "deep-analysis"
        return ""

    def _retrieve_knowledge(self, query: str) -> str:
        """
        Phase 2 增强：在 Plan 之前先做知识库检索
        如果查询涉及健康、用药、日程等，自动召回相关知识
        """
        knowledge_keywords = [
            # 健康/助理
            "药", "病", "血压", "血糖", "医院", "体检", "吃什么", "注意",
            "报告", "记录", "多少", "上次", "去年", "前次",
            # 创作/表达
            "写", "整理", "创作", "朋友圈", "文案", "照片", "故事", "绘本",
            "自传", "方案", "信", "致辞", "祝福",
            # 信息查询
            "天气", "新闻", "政策", "怎么", "哪里", "多少钱", "时间",
            # 浏览器操作
            "打开网页", "截图", "登录", "网页", "网站", "页面", "URL",
        ]
        if not any(k in query for k in knowledge_keywords):
            return ""

        try:
            from mind.knowledge import KnowledgeBase
            kb = KnowledgeBase()
            results = kb.search(query, top_k=3, min_similarity=0.25)
            kb.close()

            if not results:
                return ""

            lines = ["以下是与用户问题相关的企业知识库内容："]
            for r in results:
                sim = r.get("similarity", 0)
                text = r.get("chunk_text", "")[:350]
                lines.append(f"- [相关度{sim:.0%}] {text}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"知识库前置检索失败: {e}")
            return ""

    def _detect_accounts(self, query: str) -> List[Dict]:
        """从用户查询中检测提及的客户公司（支持内部 CRM 中的 account 名称子串匹配）。"""
        try:
            accounts = self.memory.list_accounts(owner_id=self.user_id, limit=200)
            if not accounts:
                accounts = self.memory.list_accounts(limit=200)

            matches = []
            seen = set()
            for acc in accounts:
                name = acc.get("name", "")
                if not name:
                    continue
                if name in query and name not in seen:
                    matches.append(acc)
                    seen.add(name)
            return matches
        except Exception as e:
            logger.warning(f"检测客户名称失败: {e}")
            return []

    def _retrieve_account_news(self, query: str) -> str:
        """
        销销树资讯工作台（外脑）自动召回：
        - 如果查询中包含 CRM 里的客户公司名，优先按公司名检索最新资讯
        - 如果查询中包含销售/行业信号（客户、公司、竞品、融资、行业、资讯等），用原句检索
        返回格式化摘要，供 agent 在回复前自动参考。
        """
        try:
            from mind import wechat_digest
            from mind.association_engine import detect_signals

            signals = detect_signals(query)
            accounts = self._detect_accounts(query)

            # 判定是否需要触发外脑检索
            sales_signals = {"客户", "公司", "竞品", "竞争", "融资", "人事", "高管",
                             "行业", "资讯", "动态", "新闻", "方案", "提案", "商机",
                             "报价", "价格", "跟进", "联系", "触达", "营销", "品牌"}
            has_sales_signal = bool(signals) or any(s in query for s in sales_signals)
            if not accounts and not has_sales_signal:
                return ""

            search_queries = []
            for acc in accounts:
                search_queries.append(f"{acc['name']} 最新动态")
                search_queries.append(acc["name"])
            if has_sales_signal and not accounts:
                search_queries.append(query)

            seen = set()
            results = []
            for q in search_queries[:6]:
                try:
                    for r in wechat_digest.search(q, top_k=5, min_similarity=0.22):
                        key = r.get("article_id") or r.get("link") or r.get("title")
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        results.append(r)
                except Exception as e:
                    logger.warning(f"资讯库检索失败 query={q}: {e}")
                    continue

            if not results:
                return ""

            # 按相关度排序，去重后取前 5
            results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
            top = results[:5]

            lines = ["【销销树资讯工作台】以下是与当前话题相关的最新行业资讯，请在回答时优先参考："]
            for r in top:
                title = r.get("title", "")
                account = r.get("account", "")
                date = r.get("publish_date", "")
                summary = (r.get("summary") or r.get("snippet") or "").replace("\n", " ")[:200]
                link = r.get("link") or ""
                sim = r.get("similarity", 0)
                header = f"《{title}》｜{account}｜{date}｜相关度 {sim:.0%}"
                if link:
                    lines.append(f"\n{header}\n{summary}\n来源：{link}")
                else:
                    lines.append(f"\n{header}\n{summary}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"销销树资讯工作台自动检索失败: {e}")
            return ""

    def _build_system_prompt(self, mem: dict) -> str:
        """构建 Tool Calling 模式的 system prompt（不含记忆内容，记忆通过消息注入）"""
        return f"""【铁律 — 不可违反】
- 用户询问时事、新闻、政策、热点、实时动态等时效性内容时，必须调用 search_web 工具搜索，禁止直接靠训练数据回答。
- 用户给出具体 URL 要求查看时，必须调用 fetch_webpage / jina_reader / browse_open 等网页工具，禁止假装看过。
- 不确定的信息必须搜索核实，宁可说"销销搜不到"也绝不编造。
- 没有调用工具获取实时信息之前，禁止输出"我已经查过了""最新消息是"等暗示已搜索的表述。
- **被用户纠正信息滞后、数据过时、引用过期内容后，必须立即重新调用搜索/知识库工具获取最新信息，并基于新信息给出完整答案；禁止只道歉或只说"我马上重新查"就结束回复。**
- 涉及客户公司敏感数据（财务、人事、内部信息）只读不写，不对外泄露。

你是亿科数字的销售智能助手「销销」。你可以调用工具来完成销售人员的任务。

核心定位（三层专业性赋能）：
1. 知更多：在销售人员见客户前，自动收集并整合客户公司财务、融资、高管变动、招聘方向、竞品动态、客户评价等信息，让销售从"介绍产品"变成"诊断业务"。
2. 说得准：基于联系人角色（决策者/预算人/使用者/影响者）、客户最新动态和过往沟通记录，生成个性化触达文案，并建议当前阶段应强调 ROI、技术优势还是行业案例。
3. 做得细：主动跟踪商机 next_step、客户互动频率和情绪信号，提醒销售在客户沉默或出现负面信号前主动关怀。

外脑（销销树资讯工作台）使用铁律：
- 当用户提及具体客户公司、品牌、行业或营销话题时，必须优先检索 `search_industry_news` 工具查询销销树资讯工作台中的相关文章，并将资讯中的事实作为回答依据。
- 如果资讯库信息不足、内容明显过期（与用户指出的时间不符）或需要最新一手数据，再调用 `search_web` / `browse_open` 等工具补充。
- 引用资讯库内容时必须标注文章来源（公众号/标题/发布日期/链接）。

回复风格：
- 你是销销，一个专业、干练但不失亲和力的销售智能助手，语气像一位熟悉业务的资深销售同事
- 对销售人员说话：直接、有重点、结论先行，不啰嗦
- 不确定时反问，不要瞎猜；没有可靠来源时诚实承认"这部分信息暂缺"
- 不要像机器人背数据，把信息组织成"对客户意味着什么"的洞察
- 研究/分析类任务必须执行多次搜索，换不同关键词，没找到直接承认"信息暂缺"，绝不编造
- 引用外部信息时必须标注来源，格式如 [1] 并在末尾列出来源链接
- **长报告/深度分析任务**：如果 skill 要求写长报告，必须严格遵守要求。内容不足时继续扩展，补充具体数据、案例、决策背景。必须使用 write_file 的追加模式（mode='a'）分段写入：先 mode='w' 写第一章，之后每完成一章用 mode='a' 追加，不要每次重写整份报告。
- **Skill 阶段过渡铁律**：当前加载的 skill 如果规定了分阶段执行和阶段过渡动作，必须严格遵循，不可省略。

工具使用原则：
1. 需要查信息 → 按场景选择最合适的工具（高效优先）：
   - 用户聊到具体客户/品牌/行业，或需要最新营销资讯 → 先用 `search_industry_news` 检索销销树资讯工作台
   - 用户问时事/新闻/政策/热点，但没有给具体 URL → 先用 `search_web` 搜索发现信息来源
   - 用户给了具体 URL，且是文章/博客/公告等静态页 → 用 `fetch_webpage`（快、省 token）
   - 需要省 token 且页面是文章结构 → 用 `jina_reader` 转为 Markdown
   - 动态渲染、需要登录态、需要交互（点击/填表/滚动）→ 用 `browse_open` + browse_click/fill/scroll
   - 用户说"之前看过的那个页面"、"公司那个系统" → 用 `find_chrome_url` 搜本地 Chrome 书签/历史
   - 需要原始 HTML 源码（meta、结构化数据）→ 用 `fetch_webpage`（返回原始内容）
2. 需要生成文件/PDF → 先用 write_file 写 Markdown 内容到工作目录，再调用 md_to_pdf 工具一键转换为 PDF
3. 工作目录路径：{self.work_dir} — 所有文件操作必须在此目录内
4. 需要新库 → 先用 pip_install 安装，再 execute_code
5. 数据处理、格式转换 → 用 execute_code（Python）
6. 禁止：rm/chmod/system/subprocess/eval/exec，超时30秒
7. **Skill 文件路径**：skill 文件存放在 `data/skills/<skill-name>.md`，如需读取 skill 内容，直接用此完整路径

联网策略（关键）：
- 先明确目标：用户要什么信息？什么算完成？
- 选择最可能直达的方式作为第一步验证。一次成功最好；不成功立即换方式
- fetch_webpage 失败（反爬/JS渲染）→ 换 browse_open
- jina_reader 返回内容过短或错乱 → 换 fetch_webpage 或 browse_open
- **search_web 返回结果明显不相关、为空、或全是泛化内容 → 换 web-access 其他工具兜底**：先用 `fetch_webpage` 或 `jina_reader` 抓取已知来源的详情页；如果没有可用 URL，用 `browse_open` 直接打开搜索引擎网页手动搜索，或换不同关键词再搜一次
- **搜中文商业/公司/品牌信息时 SearXNG 结果差 → 优先用 `browse_open` 打开百度或必应直接搜，中文搜索引擎的网页版质量通常优于聚合接口**
- browse_open 遇到登录墙 → 判断是否真的挡住目标：挡住了告知用户登录，没挡住就绕过
- 信息核实要找一手来源（官网、官方平台），不要用二手报道互相印证
- 拿到足够信息后停止操作，不过度浏览

搜索与写作的平衡（关键）：
- 信息收集阶段：覆盖主要来源即可，不要无限浏览
- 当已经获得足够信息后，必须立即停止，进入写作/分析/生成阶段
- 研究类任务：获取关键页面 → 立即写报告 → 生成文件。不要把所有迭代都花在浏览上
- 如果用户要求输出 PDF/文件，最终必须用 write_file 和 execute_code 生成实际文件，不能只给文字回复

复杂任务管理（todo 工具）：
- 当任务涉及 3 个以上步骤时，第一步先用 todo 工具创建任务清单
- 每个步骤一个 todo 项：{{id: "1", content: "搜索快手最新财报", status: "pending"}}
- 每完成一步，立即用 todo 工具更新状态为 completed，并标记下一步为 in_progress

计划工具（plan 工具）—— 复杂任务先规划：
- 当任务涉及 3 个以上步骤（如深度研究、分析报告、多源数据整合）时，**先调用 plan 工具制定结构化执行计划**
- 计划格式：steps=[{{"id": "1", "description": "搜索客户公司最新融资", "expected_tools": ["search_web"]}}, ...]

浏览器工具使用原则：
1. 需要读取网页内容 → 先用 browse_open 打开，再按需 browse_click/browse_scroll/browse_text
2. 涉及登录、敏感操作时，提醒用户确认
3. 截图会保存到工作目录，可用于保存证据或分享

当你需要调用工具来查资料、生成文件、处理数据时，直接调用对应工具。像真人同事一样自然，不需要每一步都解释你在做什么。"""



    def _reflect(self, query: str, observations: list) -> str:
        """总结执行结果，生成给用户的回复"""
        # 截断 observations，防止本地模型处理超时
        MAX_OBS_LEN = 6000
        context = "\n".join(observations)
        if len(context) > MAX_OBS_LEN:
            context = context[:MAX_OBS_LEN] + "\n...[内容过长，已截断]"
        try:
            return chat(
                system="你是销销，亿科数字的销售智能助手。根据执行过程，用专业、干练但亲和的销售同事口吻总结结果。\n- 结论先行，先说关键发现或建议\n- 把数据组织成""对客户/商机意味着什么""的洞察，不要罗列原始数据\n- 研究分析没搜到信息时，诚实承认""这部分信息暂缺""，不要编造\n- 不确定就反问，不要瞎猜",
                user_prompt=f"原始需求：{query}\n\n执行过程：\n{context}\n\n请总结最终回复：",
                model=MODEL_DAILY,
                max_tokens=4096,
                temperature=0.7,
            )
        except Exception as e:
            logger.error(f"Reflect 失败: {e}")
            return "销销想了想，但是有点糊涂了，您能再说一遍吗？"

    def _consolidate(self, query: str, observations: list, summary: str, thread_id: str = None):
        """沉淀记忆"""
        try:
            self.memory.consolidate(query, summary, thread_id)
        except Exception as e:
            logger.error(f"Consolidate 失败: {e}")

    def _should_use_coordinator(self, query: str, skill_name: str) -> bool:
        """判断是否使用 Coordinator 模式

        触发条件：
        1. 加载了 planning skill（hv-analysis/deep-analysis 等）
        2. 用户查询明确包含"分步骤""并行""同时查"等关键词
        """
        if skill_name in {"hv-analysis", "deep-analysis", "khazix-writer", "ppt-master"}:
            return True
        coordinator_keywords = {"分步骤", "并行", "同时查", "多角度", "分别查", "拆分", "分解"}
        if any(kw in query for kw in coordinator_keywords):
            return True
        return False

    def _run_with_coordinator(
        self,
        query: str,
        system: str,
        messages: list[AgentMessage],
        event_sink=None,
        thread_id: str = "",
    ) -> dict:
        """Coordinator 模式：根任务 + 子任务并发执行"""
        coordinator = TaskCoordinator(
            llm=self.llm,
            toolkit=self.tools,
            event_sink=event_sink,
            max_concurrent=3,
        )

        root_task = coordinator.create_task(
            goal=query,
            task_type=TaskType.COMPOSITE,
            work_dir_base=Path(os.getenv("DATA_DIR", "./data")),
        )

        # 运行根任务（传入完整 system prompt 和初始 messages）
        coordinator.run_task(
            root_task,
            system_prompt=system,
            initial_messages=messages,
        )

        # 等待所有任务完成
        coordinator.wait_for_all()

        final_reply = root_task.result or "销销处理完了，但没有拿到结果..."
        care_signals = []

        # 扫描所有任务产出文件
        generated_files = []
        try:
            for task in coordinator.task_store.all_tasks():
                for p in task.work_dir.rglob("*"):
                    if p.is_file() and p.stat().st_size > 100:
                        suffix = p.suffix.lower()
                        if suffix in (".pdf", ".png", ".jpg", ".jpeg", ".mp3", ".mp4", ".docx", ".pptx"):
                            generated_files.append(str(p))
        except Exception:
            pass

        return {
            "reply": final_reply,
            "mode": 4 if len(final_reply) > 800 else 1,
            "files": generated_files,
            "work_dir": str(root_task.work_dir),
        }

    def _save_exchange(self, thread_id: str, query: str, reply: str):
        """保存对话到情景记忆，同时写入任务工作目录的 conversation.md"""
        try:
            self.memory.save_message(thread_id, "user", query)
            self.memory.save_message(thread_id, "assistant", reply)
        except Exception as e:
            logger.error(f"保存对话失败: {e}")

        # 同时写入任务目录，便于任务历史查看完整上下文
        try:
            conv_path = self.work_dir / "conversation.md"
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(conv_path, "a", encoding="utf-8") as f:
                f.write(f"## {timestamp}\n\n**用户：**\n{query}\n\n**销销：**\n{reply}\n\n---\n\n")
        except Exception as e:
            logger.warning(f"写入任务对话记录失败: {e}")

    def _record_latest_task(self, work_dir: Path):
        """记录用户最近一次任务目录，供 Web 任务面板展示"""
        try:
            state_dir = Path(os.getenv("DATA_DIR", "./data")) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            p = state_dir / "latest_task.json"
            mapping = {}
            if p.exists():
                mapping = json.loads(p.read_text(encoding="utf-8"))
            mapping[self.user_id] = {
                "work_dir": str(work_dir.resolve()),
                "timestamp": time.time(),
            }
            p.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"记录 latest_task 失败: {e}")


# 向后兼容别名
FamilyAgent = AgentSession
