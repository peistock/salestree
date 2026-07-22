"""
销销 外挂大脑 —— Streamlit 管理界面

销售团队与 Agent 的协作界面：
- 今日托付（课前 Briefing）
- 晚间简报（课后 Debriefing）
- 紧急插话（Override）
- 记忆透明（查看/删除）
- 客户与商机
- 文档上传（知识库）

启动：streamlit run dashboard.py --server.port 8501
"""
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
load_dotenv()

import streamlit as st

# 将项目根目录加入路径
sys.path.insert(0, str(Path(__file__).parent))

from mind.memory import (
    get_all_users,
    get_user_profile,
    get_episodes,
    get_summaries,
    get_notifications,
    mark_notification_read,
    save_briefing,
    save_override,
    dismiss_override,
    get_today_briefings,
    get_today_overrides,
    delete_episodic_memory,
    get_conn,
    list_organizations,
    update_org_quota,
    summarize_llm_usage,
    list_llm_usage,
    list_users_full,
    create_user,
    update_user,
    get_user_full,
    deactivate_user,
    reactivate_user,
)
from mind.knowledge import KnowledgeBase

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "assets", "xiaoxiaoshu-logo.png")

st.set_page_config(
    page_title="销销 外挂大脑",
    page_icon=LOGO_PATH,
    layout="wide",
)

# ========== 统一配色与字体（资讯台风格） ==========
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,100..900;1,9..144,100..900&family=IBM+Plex+Mono:ital,wght@0,400;0,500;0,700;1,400&family=Noto+Serif+SC:wght@200..900&display=swap');

    :root {
      --paper: #0d0d0d !important;
      --paper-2: #1a1a1a !important;
      --card: #1c1c1e !important;
      --ink: #ffffff !important;
      --ink-2: #e5e5e7 !important;
      --ink-3: #8e8e93 !important;
      --line: #38383a !important;
      --vermilion: #d8401f !important;
      --vermilion-d: #b8311a !important;
      --moss: #34c759 !important;
      --gold: #ffcc00 !important;
    }

    html, body, .stApp, [data-testid="stAppViewContainer"] {
      background-color: var(--paper) !important;
      color: var(--ink) !important;
      font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
    }

    h1, h2, h3, h4, h5, h6,
    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3,
    [data-testid="stHeading"] h1, [data-testid="stHeading"] h2, [data-testid="stHeading"] h3 {
      color: var(--ink) !important;
      letter-spacing: -0.02em !important;
    }

    p, li, span, div, label {
      color: var(--ink-2) !important;
    }

    [data-testid="stSidebar"] {
      background-color: var(--paper-2) !important;
      border-right: 1px solid var(--line) !important;
    }

    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] div {
      color: var(--ink-2) !important;
    }

    button, .stButton>button, [data-testid="stButton"]>button,
    [data-testid="baseButton-primary"], [data-testid="baseButton-secondary"] {
      background-color: var(--vermilion) !important;
      color: #ffffff !important;
      border: 1px solid var(--vermilion-d) !important;
      border-radius: 6px !important;
      font-weight: 500 !important;
    }

    button:hover, .stButton>button:hover,
    [data-testid="baseButton-primary"]:hover {
      background-color: var(--vermilion-d) !important;
      border-color: var(--vermilion-d) !important;
    }

    input, textarea,
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stNumberInput"] input,
    [data-testid="stDateInput"] input,
    [data-baseweb="select"] > div,
    [data-testid="stSelectbox"] > div {
      background-color: var(--card) !important;
      color: var(--ink) !important;
      border: 1px solid var(--line) !important;
      border-radius: 6px !important;
    }

    [data-testid="stExpander"],
    [data-testid="stVerticalBlockBorderWrapper"] {
      background-color: var(--card) !important;
      border: 1px solid var(--line) !important;
      border-radius: 8px !important;
    }

    [data-testid="stMetric"] {
      background-color: var(--card) !important;
      border: 1px solid var(--line) !important;
      border-radius: 8px !important;
      padding: 12px !important;
    }

    [data-testid="stMetric"] [data-testid="stMetricValue"] {
      color: var(--vermilion) !important;
    }

    [data-testid="stMetric"] [data-testid="stMetricLabel"] {
      color: var(--ink-3) !important;
    }

    hr {
      border-color: var(--line) !important;
    }

    code, pre, .monospace {
      background-color: var(--paper-2) !important;
      color: var(--ink) !important;
    }

    a {
      color: var(--vermilion) !important;
      text-decoration: none !important;
    }

    a:hover {
      color: #ff6b4f !important;
      text-decoration: underline !important;
    }

    [data-testid="stTabs"] [aria-selected="true"] {
      color: var(--vermilion) !important;
      border-bottom: 2px solid var(--vermilion) !important;
    }

    [data-testid="stTabs"] [aria-selected="false"] {
      color: var(--ink-3) !important;
    }

    [data-testid="stNotificationContentInfo"],
    [data-testid="stAlertContentInfo"] {
      background-color: var(--card) !important;
      border-left: 4px solid var(--moss) !important;
      color: var(--ink-2) !important;
    }

    [data-testid="stNotificationContentWarning"],
    [data-testid="stAlertContentWarning"] {
      background-color: var(--card) !important;
      border-left: 4px solid var(--gold) !important;
      color: var(--ink-2) !important;
    }

    [data-testid="stNotificationContentError"],
    [data-testid="stAlertContentError"] {
      background-color: var(--card) !important;
      border-left: 4px solid var(--vermilion) !important;
      color: var(--ink-2) !important;
    }

    [data-testid="stSuccessMessage"] {
      background-color: var(--card) !important;
      border-left: 4px solid var(--moss) !important;
      color: var(--ink-2) !important;
    }

    ::-webkit-scrollbar {
      width: 8px !important;
      height: 8px !important;
    }

    ::-webkit-scrollbar-track {
      background: var(--paper-2) !important;
    }

    ::-webkit-scrollbar-thumb {
      background: var(--line) !important;
      border-radius: 4px !important;
    }

    ::-webkit-scrollbar-thumb:hover {
      background: var(--ink-3) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ========== 认证 ==========
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

if DASHBOARD_PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        col1, col2 = st.columns([1, 5])
        with col1:
            st.image(LOGO_PATH, width=80)
        with col2:
            st.title("销销 外挂大脑")
        pwd = st.text_input("请输入密码", type="password")
        if st.button("进入"):
            if pwd == DASHBOARD_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("密码错误")
        st.stop()

# ========== 加载数据 ==========
users = get_all_users()
member_map = {m["user_id"]: m["name"] for m in users}
user_options = [(m["user_id"], m["name"]) for m in users]

# ========== 侧边栏 ==========
st.sidebar.image(LOGO_PATH, width=60)
st.sidebar.caption("销售智能协作空间 — 管理界面")
st.sidebar.divider()

# 当前操作者
st.sidebar.subheader("🔑 当前操作者")
operator_options = {u["user_id"]: f"{u['name']}（{u['user_id']}）" for u in users}
if "operator_id" not in st.session_state or st.session_state.operator_id not in operator_options:
    # 默认固定为陈沛（sales_001），不存在则取第一个
    st.session_state.operator_id = "sales_001" if "sales_001" in operator_options else next(iter(operator_options.keys()), None)
selected_operator = st.sidebar.selectbox(
    "选择身份",
    options=list(operator_options.keys()),
    format_func=lambda x: operator_options.get(x, x),
    index=list(operator_options.keys()).index(st.session_state.operator_id) if st.session_state.operator_id in operator_options else 0,
    label_visibility="collapsed",
)
st.session_state.operator_id = selected_operator
operator = get_user_full(selected_operator) if selected_operator else None
is_operator_admin = operator.get("is_admin") if operator else False
st.sidebar.caption(f"权限：{'管理员' if is_operator_admin else '成员'}")

st.sidebar.divider()

# 销售人员
st.sidebar.subheader("销售人员")
for m in users:
    st.sidebar.write(f"👤 {m['name']} ({m['user_id']})")

st.sidebar.divider()

# 今日概览
st.sidebar.subheader("📊 今日概览")
today_briefings = get_today_briefings()
today_overrides = get_today_overrides()
unread_notes = get_notifications(unread_only=True)

st.sidebar.metric("今日托付", len(today_briefings))
st.sidebar.metric("今日插话", len([o for o in today_overrides if o["status"] == "pending"]))
st.sidebar.metric("未读通知", len(unread_notes))

st.sidebar.divider()
st.sidebar.caption(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# ========== 主界面 ==========
col1, col2 = st.columns([1, 10])
with col1:
    st.image(LOGO_PATH, width=64)
with col2:
    st.title("销销 外挂大脑")

tabs = st.tabs(["📝 今日托付", "📰 晚间简报", "🚨 紧急插话", "🔍 记忆透明", "🏢 客户与商机", "📤 文档上传", "📈 LLM 用量", "⚙️ LLM 配置"] + (["👥 用户管理"] if is_operator_admin else []))

# ---------- 今日托付 ----------
with tabs[0]:
    st.header("📝 今日托付")
    st.caption("每天早上花 2 分钟告诉 Agent 今天重点关注什么")

    for user_id, name in user_options:
        with st.expander(f"{name} 的今日关注", expanded=True):
            # 显示已有的今日托付
            existing = get_today_briefings(user_id)
            if existing:
                for b in existing:
                    col1, col2 = st.columns([0.9, 0.1])
                    with col1:
                        st.info(b["content"])
                    with col2:
                        st.caption(f"by {b['created_by']}")

            # 新增托付
            new_briefing = st.text_area(
                f"写今日关注...",
                key=f"briefing_input_{user_id}",
                placeholder=f"例如：今天重点跟进{name}负责的快手方案",
                label_visibility="collapsed",
            )
            if st.button(f"保存 {name} 的托付", key=f"save_briefing_{user_id}"):
                if new_briefing.strip():
                    save_briefing(user_id, new_briefing.strip(), "dashboard")
                    st.success("已保存")
                    st.rerun()
                else:
                    st.warning("内容不能为空")

# ---------- 晚间简报 ----------
with tabs[1]:
    st.header("📰 晚间简报")
    st.caption("Agent 每晚 21:00 自动生成的销售日报")

    col1, col2 = st.columns([0.3, 0.7])
    with col1:
        selected_date = st.date_input("选择日期", date.today())
        show_all = st.checkbox("显示已读", value=True)

    notes = get_notifications(
        user_id=None,
        unread_only=not show_all,
        limit=50,
    )

    if not notes:
        st.info("暂无通知")
    else:
        for note in notes:
            # 按日期过滤（简化：只显示当天）
            note_date = note["created_at"].date() if hasattr(note["created_at"], "date") else note["created_at"]
            if isinstance(note_date, datetime):
                note_date = note_date.date()
            if note_date != selected_date:
                continue

            icon = {"debriefing": "📖", "alert": "🚨", "info": "ℹ️"}.get(note["type"], "📌")
            with st.container(border=True):
                c1, c2 = st.columns([0.85, 0.15])
                with c1:
                    st.subheader(f"{icon} {note['title']}")
                with c2:
                    if not note["is_read"]:
                        if st.button("标记已读", key=f"read_{note['id']}"):
                            mark_notification_read(note["id"])
                            st.rerun()
                st.write(note["content"])
                st.caption(f"{note['created_at']}")

# ---------- 紧急插话 ----------
with tabs[2]:
    st.header("🚨 紧急插话")
    st.caption("高优先级指令，Agent 下一次交互立即响应")

    # 显示今日已有的 overrides
    st.subheader("今日指令")
    today_ov = get_today_overrides()
    if today_ov:
        for ov in today_ov:
            priority_color = {3: "🔴", 2: "🟠", 1: "🟡"}.get(ov["priority"], "⚪")
            status_color = {
                "pending": "⏳ 待执行",
                "applied": "✅ 已执行",
                "dismissed": "❌ 已取消",
            }.get(ov["status"], ov["status"])
            with st.container(border=True):
                st.write(f"{priority_color} **{member_map.get(ov['user_id'], ov['user_id'])}**：{ov['content']}")
                st.caption(f"优先级 {ov['priority']} | {status_color} | by {ov['created_by']} | {ov['created_at']}")
                if ov["status"] == "pending":
                    if st.button("取消", key=f"dismiss_{ov['id']}"):
                        dismiss_override(ov["id"])
                        st.rerun()
    else:
        st.info("今日暂无指令")

    st.divider()
    st.subheader("发送新指令")

    target_user = st.selectbox("选择用户", options=[uid for uid, _ in user_options], format_func=lambda x: member_map.get(x, x))
    override_content = st.text_area("指令内容", placeholder="例如：客户刚才提到预算紧张，你下次跟进时重点讲 ROI")
    priority = st.slider("优先级", 1, 3, 2, format="%d", help="3=立即 2=紧急 1=普通")

    if st.button("📤 发送指令"):
        if override_content.strip():
            save_override(target_user, override_content.strip(), priority, "dashboard")
            st.success("指令已发送，Agent 下一次交互会优先处理")
            st.rerun()
        else:
            st.warning("内容不能为空")

# ---------- 记忆透明 ----------
with tabs[3]:
    st.header("🔍 记忆透明")
    st.caption("用户可以随时问'你都知道我些什么'，这里就是你看到的全部")

    selected_member = st.selectbox(
        "选择查看对象",
        options=[uid for uid, _ in user_options],
        format_func=lambda x: member_map.get(x, x),
        key="memory_select",
    )

    if selected_member:
        profile = get_user_profile(selected_member)
        name = profile.get("name", selected_member)

        # 画像
        st.subheader(f"👤 {name} 的画像")
        with st.container(border=True):
            for key, value in profile.items():
                if value and str(value) not in ("", "None", "[]", "{}", "无"):
                    st.write(f"**{key}**: {value}")

        # 最近对话
        st.subheader("💬 最近对话")
        episodes = get_episodes(selected_member, limit=20)
        if episodes:
            for ep in episodes:
                with st.container(border=True):
                    role_label = "👤 用户" if ep["role"] == "user" else "🤖 Agent"
                    st.write(f"{role_label}: {ep['content'][:300]}")
                    st.caption(f"{ep['created_at']} | id={ep['id']}")
                    if st.button("删除", key=f"del_ep_{ep['id']}"):
                        if delete_episodic_memory(ep["id"]):
                            st.success("已删除")
                            st.rerun()
                        else:
                            st.error("删除失败")
        else:
            st.info("暂无对话记录")

        # 记忆摘要
        st.subheader("📚 记忆摘要")
        summaries = get_summaries(selected_member, limit=10)
        if summaries:
            for s in summaries:
                with st.container(border=True):
                    st.write(f"**[{s['summary_type']}]** {s['summary_text'][:200]}")
                    st.caption(f"{s['created_at']}")
        else:
            st.info("暂无摘要")

# ---------- 客户与商机 ----------
with tabs[4]:
    st.header("🏢 客户与商机")
    st.caption("管理客户公司、联系人和商机")

    from mind.memory import Memory

    m = Memory("dashboard", "dashboard")

    # 加载销售人员选项
    try:
        conn = get_conn()
        with conn.cursor() as c:
            c.execute("SELECT user_id, name FROM user_profiles WHERE entity_type='sales' ORDER BY name;")
            sales_users = {row[0]: row[1] for row in c.fetchall()}
        conn.close()
    except Exception as e:
        st.error(f"加载销售人员失败: {e}")
        sales_users = {}

    # 新增客户
    with st.expander("➕ 新增客户"):
        with st.form("add_account"):
            acc_name = st.text_input("公司名 *")
            acc_industry = st.text_input("行业")
            acc_stage = st.selectbox("阶段", ["prospect", "qualified", "proposal", "negotiation", "closed-won", "closed-lost"])
            acc_region = st.text_input("地区")
            acc_notes = st.text_area("备注")
            acc_owner = st.selectbox("负责人", options=list(sales_users.keys()), format_func=lambda x: sales_users.get(x, x)) if sales_users else st.text_input("负责人 ID")
            submitted = st.form_submit_button("保存客户")
            if submitted:
                if not acc_name.strip():
                    st.error("公司名不能为空")
                else:
                    import uuid
                    account_id = f"acc_{uuid.uuid4().hex[:8]}"
                    owner = acc_owner if isinstance(acc_owner, str) else acc_owner
                    if m.create_account(account_id, acc_name.strip(), industry=acc_industry or None, stage=acc_stage, region=acc_region or None, notes=acc_notes or None, owner_id=owner or None):
                        st.success(f"已保存客户：{acc_name}")
                        st.rerun()
                    else:
                        st.error("保存客户失败")

    # 客户列表
    try:
        accounts = m.list_accounts(limit=100)
    except Exception as e:
        st.error(f"加载客户列表失败: {e}")
        accounts = []

    if not accounts:
        st.info("暂无客户")
    else:
        account_options = {f"{a['name']}（{a['account_id']}）{ ' - ' + (sales_users.get(a['owner_id'], a['owner_id']) if a.get('owner_id') else '未分配') }": a for a in accounts}
        selected_label = st.selectbox("选择客户", options=list(account_options.keys()))
        account = account_options[selected_label]

        st.subheader("客户详情")

        col_del, col_edit, col_spacer = st.columns([0.15, 0.15, 0.7])
        with col_del:
            if st.button("🗑️ 删除客户", key=f"del_acc_{account['account_id']}", type="primary"):
                if m.delete_account(account["account_id"]):
                    st.success("已删除客户")
                    st.rerun()
                else:
                    st.error("删除失败")
        with col_edit:
            with st.popover("✏️ 编辑客户"):
                with st.form(f"edit_account_{account['account_id']}"):
                    edit_acc_name = st.text_input("公司名", value=account.get("name", ""))
                    edit_acc_industry = st.text_input("行业", value=account.get("industry") or "")
                    edit_acc_stage = st.selectbox(
                        "阶段",
                        ["prospect", "qualified", "proposal", "negotiation", "closed-won", "closed-lost"],
                        index=["prospect", "qualified", "proposal", "negotiation", "closed-won", "closed-lost"].index(account.get("stage", "prospect")) if account.get("stage") in ["prospect", "qualified", "proposal", "negotiation", "closed-won", "closed-lost"] else 0,
                    )
                    edit_acc_region = st.text_input("地区", value=account.get("region") or "")
                    edit_acc_owner = st.selectbox(
                        "归属销售",
                        options=list(sales_users.keys()),
                        format_func=lambda x: sales_users.get(x, x),
                        index=list(sales_users.keys()).index(account["owner_id"]) if account.get("owner_id") in sales_users else 0,
                    ) if sales_users else st.text_input("归属销售 ID", value=account.get("owner_id") or "")
                    edit_acc_notes = st.text_area("备注", value=account.get("notes") or "")
                    edit_submitted = st.form_submit_button("保存修改")
                    if edit_submitted:
                        owner = edit_acc_owner if isinstance(edit_acc_owner, str) else edit_acc_owner
                        if m.update_account(
                            account["account_id"],
                            name=edit_acc_name.strip(),
                            industry=edit_acc_industry.strip() or None,
                            stage=edit_acc_stage,
                            region=edit_acc_region.strip() or None,
                            owner_id=owner or None,
                            notes=edit_acc_notes.strip() or None,
                        ):
                            st.success("已更新客户")
                            st.rerun()
                        else:
                            st.error("更新失败")

        st.json(account)

        col1, col2 = st.columns(2)

        # 联系人
        with col1:
            st.subheader("👤 联系人")
            try:
                contacts = m.list_contacts_by_account(account["account_id"])
            except Exception as e:
                st.error(f"加载联系人失败: {e}")
                contacts = []
            if contacts:
                for ct in contacts:
                    with st.container(border=True):
                        st.write(f"**{ct['name']}** {ct['title'] or ''}")
                        st.caption(f"{ct['department'] or ''} | {ct['role_in_deal'] or ''} | {ct['email'] or ''}")
            else:
                st.info("暂无联系人")

            with st.expander("➕ 新增联系人"):
                with st.form("add_contact"):
                    ct_name = st.text_input("姓名 *")
                    ct_title = st.text_input("职位")
                    ct_dept = st.text_input("部门")
                    ct_role = st.selectbox("角色", ["decision", "budget", "user", "influencer", "other"])
                    ct_email = st.text_input("邮箱")
                    ct_phone = st.text_input("电话")
                    ct_notes = st.text_area("备注")
                    ct_submitted = st.form_submit_button("保存联系人")
                    if ct_submitted:
                        if not ct_name.strip():
                            st.error("姓名不能为空")
                        else:
                            contact_id = f"ct_{uuid.uuid4().hex[:8]}"
                            if m.create_contact(contact_id, account["account_id"], ct_name.strip(), title=ct_title or None, department=ct_dept or None, role_in_deal=ct_role, email=ct_email or None, phone=ct_phone or None, notes=ct_notes or None):
                                st.success(f"已保存联系人：{ct_name}")
                                st.rerun()
                            else:
                                st.error("保存联系人失败")

        # 商机
        with col2:
            st.subheader("💼 商机")
            try:
                deals = m.list_deals_by_account(account["account_id"])
            except Exception as e:
                st.error(f"加载商机失败: {e}")
                deals = []
            if deals:
                for d in deals:
                    with st.container(border=True):
                        st.write(f"**{d['name']}**")
                        st.caption(f"阶段：{d['stage']} | 预计金额：{d['expected_value'] or '-'} | 下一步：{d['next_step'] or '-'}")
            else:
                st.info("暂无商机")

            with st.expander("➕ 新增商机"):
                with st.form("add_deal"):
                    d_name = st.text_input("商机名 *")
                    d_stage = st.selectbox("阶段", ["qualification", "proposal", "negotiation", "closed-won", "closed-lost"])
                    d_service = st.text_input("业务线")
                    d_value = st.number_input("预计金额", min_value=0, step=10000)
                    d_next = st.text_input("下一步")
                    d_owner = st.selectbox("负责人", options=list(sales_users.keys()), format_func=lambda x: sales_users.get(x, x)) if sales_users else st.text_input("负责人 ID")
                    d_submitted = st.form_submit_button("保存商机")
                    if d_submitted:
                        if not d_name.strip():
                            st.error("商机名不能为空")
                        else:
                            deal_id = f"deal_{uuid.uuid4().hex[:8]}"
                            owner = d_owner if isinstance(d_owner, str) else d_owner
                            if m.create_deal(deal_id, account["account_id"], d_name.strip(), stage=d_stage, service_line=d_service or None, expected_value=d_value if d_value > 0 else None, next_step=d_next or None, owner_id=owner or None):
                                st.success(f"已保存商机：{d_name}")
                                st.rerun()
                            else:
                                st.error("保存商机失败")

# ---------- 文档上传 ----------
with tabs[5]:
    st.header("📤 文档上传")
    st.caption("上传 PDF/txt/md 到销售知识库，Agent 回答时会自动引用")

    uploaded_files = st.file_uploader(
        "选择文件",
        type=["pdf", "txt", "md"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        kb = KnowledgeBase()
        progress = st.progress(0)
        for i, file in enumerate(uploaded_files):
            try:
                result = kb.ingest_bytes(file.getvalue(), file.name)
                if "error" in result:
                    st.error(f"❌ {file.name}: {result['error']}")
                else:
                    st.success(f"✅ {file.name}: {result['chunks']} 个片段已入库")
            except Exception as e:
                st.error(f"❌ {file.name}: {e}")
            progress.progress((i + 1) / len(uploaded_files))
        kb.close()
        progress.empty()

    # 已上传文档列表
    st.subheader("📚 已入库文档")
    try:
        kb = KnowledgeBase()
        docs = kb.list_docs()
        if docs:
            for doc in docs:
                col1, col2 = st.columns([0.8, 0.2])
                with col1:
                    st.write(f"📄 {doc['filename']}（id={doc['id']}）")
                with col2:
                    if st.button("删除", key=f"del_doc_{doc['id']}"):
                        if kb.delete_doc(doc["id"]):
                            st.success("已删除")
                            st.rerun()
                        else:
                            st.error("删除失败")
        else:
            st.info("知识库为空")
        kb.close()
    except Exception as e:
        st.error(f"加载文档列表失败: {e}")

# ---------- LLM 用量 ----------
with tabs[6]:
    st.header("📈 LLM 用量")
    st.caption("按组织、用户、时间维度查看 LLM token 消耗")

    try:
        orgs = list_organizations()
        org_options = {o["org_id"]: f"{o['name']}（{o['org_id']}）" for o in orgs}
        org_options[""] = "全部组织"

        # 加载用户选项
        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as c:
                c.execute("SELECT user_id, name FROM user_profiles WHERE status='active' ORDER BY name")
                all_users = c.fetchall()
        finally:
            conn.close()
        user_options = {u["user_id"]: f"{u['name']}（{u['user_id']}）" for u in all_users}
        user_options[""] = "全部用户"

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            selected_org = st.selectbox("组织", options=[""] + list(org_options.keys()), format_func=lambda x: org_options.get(x, x), key="usage_org")
        with col2:
            selected_user = st.selectbox("用户", options=[""] + list(user_options.keys()), format_func=lambda x: user_options.get(x, x), key="usage_user")
        with col3:
            start_date = st.date_input("开始日期", date.today().replace(day=1), key="usage_start")
        with col4:
            end_date = st.date_input("结束日期", date.today(), key="usage_end")

        filters = {
            "org_id": selected_org or None,
            "user_id": selected_user or None,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        }

        summary = summarize_llm_usage(**filters)

        # 指标卡
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("请求数", summary["count"])
        with m2:
            st.metric("总 Tokens", f"{summary['total_tokens']:,}")
        with m3:
            st.metric("Input", f"{summary['input_tokens']:,}")
        with m4:
            st.metric("Output", f"{summary['output_tokens']:,}")

        # 组织配额进度
        if selected_org:
            org = next((o for o in orgs if o["org_id"] == selected_org), None)
            if org:
                quota = org["monthly_token_quota"]
                used = org["used_tokens"]
                pct = min(100.0, used / quota * 100) if quota > 0 else 0
                st.progress(pct / 100, text=f"本月已用 {used:,} / {quota:,} tokens（{pct:.1f}%）")

                with st.expander("修改月度配额"):
                    new_quota = st.number_input("月度 token 配额", min_value=0, value=int(quota), step=100000, key=f"quota_{selected_org}")
                    if st.button("保存配额", key=f"save_quota_{selected_org}"):
                        if update_org_quota(selected_org, int(new_quota)):
                            st.success("配额已更新")
                            st.rerun()
                        else:
                            st.error("更新失败")

        # 按 model 分组
        if summary["by_model"]:
            st.subheader("按模型分组")
            model_df_data = []
            for m in summary["by_model"]:
                model_df_data.append({
                    "模型": m["model"],
                    "Provider": m["provider"],
                    "请求数": int(m["count"]),
                    "Input Tokens": int(m["input_tokens"]),
                    "Output Tokens": int(m["output_tokens"]),
                    "总 Tokens": int(m["total_tokens"]),
                })
            st.dataframe(model_df_data, use_container_width=True)

            # 简单柱状图
            chart_data = {row["模型"]: row["总 Tokens"] for row in model_df_data}
            if chart_data:
                st.bar_chart(chart_data)

        # 明细
        st.subheader("用量明细")
        detail = list_llm_usage(limit=100, **filters)
        if detail["rows"]:
            detail_data = []
            for r in detail["rows"]:
                detail_data.append({
                    "时间": r["created_at"].strftime("%Y-%m-%d %H:%M") if hasattr(r["created_at"], "strftime") else r["created_at"],
                    "用户": r["user_id"],
                    "模型": r["model"],
                    "Provider": r["provider"],
                    "Input": int(r["input_tokens"]),
                    "Output": int(r["output_tokens"]),
                    "Total": int(r["total_tokens"]),
                })
            st.dataframe(detail_data, use_container_width=True)
            st.caption(f"共 {detail['total']} 条，当前展示前 100 条")
        else:
            st.info("暂无用量记录")
    except Exception as e:
        st.error(f"加载用量数据失败: {e}")

# ---------- LLM 配置 ----------
with tabs[7]:
    st.header("⚙️ LLM 配置")
    st.caption("只配置你跟销销对话用的大模型；资讯分析等公共功能仍走全局默认配置")

    def _mask_key(k: str) -> str:
        if not k or len(k) <= 8:
            return k or "未设置"
        return k[:4] + "****" + k[-4:]

    _LLM_DEFAULTS = {
        "kimi": {"baseUrl": "https://api.kimi.com/coding/v1", "model": "k2.6"},
        "deepseek": {"baseUrl": "https://api.deepseek.com/v1", "model": "deepseek-v4-flash"},
        "agnes": {"baseUrl": "https://apihub.agnes-ai.com/v1", "model": "agnes-2.0-flash"},
        "custom": {"baseUrl": "", "model": ""},
    }

    try:
        # 可选用户：管理员可改任何人，成员只能改自己
        if is_operator_admin:
            llm_user_options = [(u["user_id"], f"{u['name']}（{u['user_id']}）") for u in users]
            llm_user_id = st.selectbox(
                "选择要配置的用户",
                options=[x[0] for x in llm_user_options],
                format_func=lambda x: next((l[1] for l in llm_user_options if l[0] == x), x),
                key="llm_user_select",
            )
        else:
            llm_user_id = selected_operator
            st.write(f"当前用户：**{operator.get('name') if operator else llm_user_id}**")

        llm_user = get_user_full(llm_user_id) if llm_user_id else None
        current_cfg = (llm_user.get("llm_config") or {}) if llm_user else {}
        current_key = current_cfg.get("apiKey", "")
        key_mask = _mask_key(current_key)

        pkey = f"llm_provider_select_{llm_user_id}"
        bkey = f"llm_base_url_{llm_user_id}"
        mkey = f"llm_model_{llm_user_id}"

        current_provider = current_cfg.get("provider", "kimi")
        if current_provider not in _LLM_DEFAULTS:
            current_provider = "kimi"

        def _on_provider_change(uid: str):
            p = st.session_state.get(f"llm_provider_select_{uid}", "kimi")
            st.session_state[f"llm_base_url_{uid}"] = _LLM_DEFAULTS.get(p, {}).get("baseUrl", "")
            st.session_state[f"llm_model_{uid}"] = _LLM_DEFAULTS.get(p, {}).get("model", "")

        # 初始化 session_state
        if bkey not in st.session_state:
            st.session_state[bkey] = current_cfg.get("baseUrl") or _LLM_DEFAULTS[current_provider]["baseUrl"]
        if mkey not in st.session_state:
            st.session_state[mkey] = current_cfg.get("modelDaily") or _LLM_DEFAULTS[current_provider]["model"]

        provider = st.selectbox(
            "Provider",
            options=list(_LLM_DEFAULTS.keys()),
            index=list(_LLM_DEFAULTS.keys()).index(current_provider),
            key=pkey,
            on_change=lambda: _on_provider_change(llm_user_id),
        )

        with st.form(f"llm_config_{llm_user_id}"):
            use_custom = st.checkbox("启用自定义 LLM 配置", value=bool(current_cfg.get("enabled")))
            base_url = st.text_input("Base URL", key=bkey)
            api_key = st.text_input(
                "API Key",
                value="",
                placeholder=f"当前已设置: {key_mask}" if current_key else "留空表示保持当前值；输入新值将覆盖",
                type="password",
            )
            model = st.text_input("对话模型", key=mkey, help="日常对话和复杂任务都用这个模型")

            save_llm = st.form_submit_button("保存配置")
            if save_llm:
                final_key = api_key.strip() if api_key.strip() else current_key
                if use_custom:
                    new_cfg = {
                        "enabled": True,
                        "provider": st.session_state[pkey],
                        "baseUrl": st.session_state[bkey].strip(),
                        "apiKey": final_key,
                        "modelDaily": st.session_state[mkey].strip(),
                        "modelComplex": st.session_state[mkey].strip(),
                    }
                else:
                    new_cfg = {"enabled": False}
                if update_user(llm_user_id, llm_config=new_cfg):
                    st.success("LLM 配置已保存")
                    st.rerun()
                else:
                    st.error("保存失败")

        if current_key:
            st.caption(f"当前 API Key 末尾：{key_mask}")
    except Exception as e:
        st.error(f"加载 LLM 配置失败: {e}")

# ---------- 用户管理 ----------
if is_operator_admin:
    with tabs[8]:
        st.header("👥 用户管理")
        st.caption("创建、编辑、禁用用户，并分配组织")

        try:
            orgs = list_organizations()
            org_options = {o["org_id"]: f"{o['name']}（{o['org_id']}）" for o in orgs}

            # 新建用户
            with st.expander("➕ 新建用户"):
                with st.form("create_user"):
                    new_user_id = st.text_input("用户 ID（留空自动生成）", placeholder="例如 sales_003")
                    new_name = st.text_input("姓名 *")
                    new_role = st.text_input("角色", value="成员")
                    new_entity = st.selectbox("类型", ["sales", "user"], index=0)
                    new_is_admin = st.checkbox("管理员权限", value=False)
                    new_org = st.selectbox("组织", options=list(org_options.keys()), format_func=lambda x: org_options.get(x, x))
                    new_team = st.text_input("团队 ID（可选）")
                    new_wechat = st.text_input("企微 ID（可选）")
                    submitted = st.form_submit_button("创建用户")
                    if submitted:
                        if not new_name.strip():
                            st.error("姓名不能为空")
                        else:
                            uid = new_user_id.strip() or f"user_{uuid.uuid4().hex[:8]}"
                            if create_user(uid, new_name.strip(), new_role, new_entity, new_org, new_team or None, new_wechat or None, new_is_admin):
                                st.success(f"已创建用户：{uid}")
                                st.rerun()
                            else:
                                st.error("用户 ID 已存在或创建失败")

            # 用户列表
            users = list_users_full()
            if users:
                st.subheader("用户列表")
                df_data = []
                for u in users:
                    df_data.append({
                        "用户 ID": u["user_id"],
                        "姓名": u["name"],
                        "角色": u["role"],
                        "类型": u["entity_type"],
                        "管理员": "是" if u.get("is_admin") else "否",
                        "组织": u.get("org_name") or u.get("org_id") or "-",
                        "团队": u["team_id"] or "-",
                        "状态": u["status"],
                        "创建时间": u["created_at"].strftime("%Y-%m-%d %H:%M") if hasattr(u["created_at"], "strftime") else u["created_at"],
                    })
                st.dataframe(df_data, use_container_width=True)

                # 编辑/禁用操作
                st.subheader("编辑用户")
                edit_uid = st.selectbox("选择用户", options=[u["user_id"] for u in users], format_func=lambda x: f"{next((u['name'] for u in users if u['user_id']==x), x)}（{x}）")
                if edit_uid:
                    edit_user = get_user_full(edit_uid)
                    if edit_user:
                        with st.form(f"edit_user_{edit_uid}"):
                            edit_name = st.text_input("姓名", value=edit_user["name"])
                            edit_role = st.text_input("角色", value=edit_user["role"])
                            edit_entity = st.selectbox(
                                "类型",
                                options=["sales", "user"],
                                index=0 if edit_user["entity_type"] == "sales" else 1,
                            )
                            edit_is_admin = st.checkbox("管理员权限", value=bool(edit_user.get("is_admin")))
                            edit_org = st.selectbox(
                                "组织",
                                options=list(org_options.keys()),
                                format_func=lambda x: org_options.get(x, x),
                                index=list(org_options.keys()).index(edit_user["org_id"]) if edit_user["org_id"] in org_options else 0,
                            )
                            edit_team = st.text_input("团队 ID（可选）", value=edit_user["team_id"] or "")
                            edit_status = st.selectbox("状态", ["active", "disabled"], index=0 if edit_user["status"] == "active" else 1)
                            save_submitted = st.form_submit_button("保存修改")
                            if save_submitted:
                                if update_user(
                                    edit_uid,
                                    name=edit_name.strip(),
                                    role=edit_role.strip(),
                                    entity_type=edit_entity,
                                    is_admin=edit_is_admin,
                                    org_id=edit_org,
                                    team_id=edit_team.strip() or None,
                                    status=edit_status,
                                ):
                                    st.success("已更新")
                                    st.rerun()
                                else:
                                    st.error("更新失败")
            else:
                st.info("暂无用户")
        except Exception as e:
            st.error(f"加载用户数据失败: {e}")
