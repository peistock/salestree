"""
关联引擎（Phase 2.x）
- 输入信号 → 激活相关记忆节点 → 组装关联上下文
- 规则驱动 + LLM 增强
"""
import os
import logging
from typing import List, Dict, Optional
from datetime import datetime

from mind.memory import get_conn
from mind.llm_client import chat

logger = logging.getLogger(__name__)

MODEL_DAILY = os.getenv("MODEL_DAILY", "deepseek-chat")

# ========== 关联规则表 ==========
# 关键词 → 需要关联的销售记忆类型（多对多）
ASSOCIATION_RULES = {
    # 客户/公司
    "客户": ["account.basic", "account.signals", "deal.stage"],
    "公司": ["account.basic", "account.signals", "deal.stage"],

    # 跟进/联系
    "跟进": ["contact.last_touch", "activity.recent", "deal.next_step"],
    "联系": ["contact.last_touch", "activity.recent", "deal.next_step"],
    "触达": ["contact.last_touch", "activity.recent", "deal.next_step"],

    # 报价/价格
    "报价": ["deal.expected_value", "competitor.pricing"],
    "价格": ["deal.expected_value", "competitor.pricing"],

    # 竞品/竞争
    "竞品": ["competitor.pricing", "competitor.reviews", "account.signals"],
    "竞争": ["competitor.pricing", "competitor.reviews", "account.signals"],

    # 融资/人事/高管动态
    "融资": ["account.signals"],
    "人事": ["account.signals"],
    "高管": ["account.signals"],

    # 方案/提案/合同/商机
    "方案": ["deal.stage", "activity.recent"],
    "提案": ["deal.stage", "activity.recent"],
    "合同": ["deal.stage", "activity.recent"],
    "签约": ["deal.stage", "activity.recent"],
    "商机": ["deal.stage", "deal.expected_value"],

    # 行业/资讯/新闻
    "行业": ["account.signals"],
    "资讯": ["account.signals"],
    "动态": ["account.signals"],
    "新闻": ["account.signals"],

    # 营销/品牌
    "营销": ["account.signals", "deal.stage"],
    "品牌": ["account.signals", "deal.stage"],
}

# 记忆类型 → 查询 SQL 模板
MEMORY_QUERIES = {
    "account.basic": """
        SELECT name, industry, stage, region, notes
        FROM accounts
        WHERE owner_id=%s
        ORDER BY updated_at DESC LIMIT 3
    """,
    "account.signals": """
        SELECT a.name AS account_name, ac.activity_type, ac.content, ac.created_at
        FROM activities ac
        JOIN accounts a ON ac.entity_id = a.account_id
        WHERE ac.entity_type = 'account' AND a.owner_id=%s
        ORDER BY ac.created_at DESC LIMIT 5
    """,
    "deal.stage": """
        SELECT a.name AS account_name, d.name AS deal_name, d.stage,
               d.expected_value, d.next_step, d.updated_at
        FROM deals d
        JOIN accounts a ON d.account_id = a.account_id
        WHERE d.owner_id=%s
        ORDER BY d.updated_at DESC LIMIT 5
    """,
    "deal.next_step": """
        SELECT a.name AS account_name, d.name AS deal_name, d.stage,
               d.expected_value, d.next_step, d.updated_at
        FROM deals d
        JOIN accounts a ON d.account_id = a.account_id
        WHERE d.owner_id=%s AND d.next_step IS NOT NULL AND d.next_step <> ''
        ORDER BY d.updated_at DESC LIMIT 5
    """,
    "deal.expected_value": """
        SELECT a.name AS account_name, d.name AS deal_name, d.expected_value, d.stage
        FROM deals d
        JOIN accounts a ON d.account_id = a.account_id
        WHERE d.owner_id=%s
        ORDER BY d.updated_at DESC LIMIT 5
    """,
    "contact.last_touch": """
        SELECT a.name AS account_name, c.name AS contact_name, c.title, c.department, c.updated_at
        FROM contacts c
        JOIN accounts a ON c.account_id = a.account_id
        WHERE a.owner_id=%s
        ORDER BY c.updated_at DESC LIMIT 5
    """,
    "activity.recent": """
        SELECT activity_type, direction, content, created_at
        FROM activities
        WHERE owner_id=%s
        ORDER BY created_at DESC LIMIT 5
    """,
    "competitor.pricing": """
        SELECT content, created_at FROM episodic_memory
        WHERE user_id=%s AND role='user'
          AND (content ILIKE '%价格%' OR content ILIKE '%报价%' OR content ILIKE '%费用%')
        ORDER BY created_at DESC LIMIT 3
    """,
    "competitor.reviews": """
        SELECT content, created_at FROM episodic_memory
        WHERE user_id=%s AND role='user'
          AND (content ILIKE '%竞品%' OR content ILIKE '%竞争%' OR content ILIKE '%对手%')
        ORDER BY created_at DESC LIMIT 3
    """,
}


def detect_signals(query: str) -> List[str]:
    """从查询中检测关联信号"""
    signals = []
    for keyword, _ in ASSOCIATION_RULES.items():
        if keyword in query:
            signals.append(keyword)
    return signals


def retrieve_memory_fragments(user_id: str, memory_types: List[str]) -> Dict[str, List[str]]:
    """根据记忆类型检索相关片段"""
    conn = get_conn()
    fragments = {}
    try:
        with conn.cursor() as c:
            for mt in memory_types:
                sql = MEMORY_QUERIES.get(mt)
                if not sql:
                    continue
                c.execute(sql, (user_id,))
                rows = c.fetchall()
                texts = []
                for row in rows:
                    text = " ".join(str(x) for x in row if x)
                    if text.strip():
                        texts.append(text[:200])
                if texts:
                    fragments[mt] = texts
    except Exception as e:
        logger.error(f"检索记忆片段失败: {e}")
    finally:
        conn.close()
    return fragments


def build_association_context(user_id: str, query: str, user_name: str = "") -> str:
    """
    主入口：输入用户查询，输出关联上下文字符串
    如果没有关联信号，返回空字符串（不干扰原有流程）
    """
    signals = detect_signals(query)
    if not signals:
        return ""

    # 收集所有需要检索的记忆类型
    all_types = set()
    for sig in signals:
        all_types.update(ASSOCIATION_RULES.get(sig, []))

    if not all_types:
        return ""

    # 检索记忆片段
    fragments = retrieve_memory_fragments(user_id, list(all_types))
    if not fragments:
        return ""

    # 组装上下文
    lines = ["【关联记忆】"]
    for mt, texts in fragments.items():
        label_map = {
            "account.basic": "客户基础",
            "account.signals": "客户动态",
            "deal.stage": "商机阶段",
            "deal.next_step": "下一步",
            "deal.expected_value": "商机金额",
            "contact.last_touch": "联系人",
            "activity.recent": "最近活动",
            "competitor.pricing": "竞品价格",
            "competitor.reviews": "竞品口碑",
        }
        label = label_map.get(mt, mt.replace("_", "."))
        for t in texts:
            lines.append(f"  • [{label}] {t}")

    context = "\n".join(lines)
    logger.info(f"关联引擎激活: user={user_id}, signals={signals}, types={all_types}")
    return context


def build_llm_enhanced_context(user_id: str, query: str, user_name: str = "", max_tokens: int = 500) -> str:
    """
    增强版：用 LLM 对关联记忆进行综合分析，生成更自然的上下文
    用于复杂场景（如头晕分析），简单场景用 build_association_context 即可
    """
    base_context = build_association_context(user_id, query, user_name)
    if not base_context:
        return ""

    try:
        analysis = chat(
            system="你是销售助手的关联分析助手。根据以下关联记忆，用1-2句话简洁分析它们与用户当前问题的可能联系。不要下诊断，只陈述事实关联。",
            user_prompt=f"用户「{user_name}」说：{query}\n\n检索到的关联记忆：\n{base_context}\n\n请分析这些记忆与用户当前问题的关联：",
            model=MODEL_DAILY,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        if analysis:
            return f"【关联分析】{analysis}\n\n{base_context}"
    except Exception as e:
        logger.warning(f"LLM 关联增强失败，回退到基础模式: {e}")

    return base_context
