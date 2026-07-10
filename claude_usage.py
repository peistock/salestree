"""
Claude Code Token 用量看板
扫描 ~/.claude/projects/ 下的会话 JSONL，提取 usage 统计

用法：
  python claude_usage.py              # 命令行报表
  streamlit run claude_usage.py       # 可视化看板
"""
import json
import glob
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, date

# 尝试导入 streamlit，不存在则降级为命令行
try:
    import streamlit as st
    HAS_STREAMLIT = True
except ImportError:
    HAS_STREAMLIT = False


CLaude_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def parse_all_sessions():
    """遍历所有 JSONL，提取 assistant 消息的 usage 数据。"""
    pattern = os.path.join(CLaude_PROJECTS_DIR, "*", "*.jsonl")
    pattern2 = os.path.join(CLaude_PROJECTS_DIR, "*", "*", "*.jsonl")
    files = glob.glob(pattern) + glob.glob(pattern2)

    records = []  # 每条一次 API 调用
    for filepath in files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if data.get("type") != "assistant":
                        continue

                    msg = data.get("message", {})
                    usage = msg.get("usage")
                    if not usage:
                        continue

                    ts = data.get("timestamp", "")
                    dt = None
                    if ts:
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except ValueError:
                            pass

                    # 从路径推断项目名
                    parts = filepath.replace(CLaude_PROJECTS_DIR, "").strip("/").split("/")
                    project = parts[0] if parts else "unknown"

                    records.append({
                        "timestamp": dt,
                        "date": dt.strftime("%Y-%m-%d") if dt else "unknown",
                        "model": msg.get("model", "unknown"),
                        "input_tokens": usage.get("input_tokens", 0) or 0,
                        "output_tokens": usage.get("output_tokens", 0) or 0,
                        "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
                        "project": project,
                        "filepath": filepath,
                    })
        except Exception:
            continue

    return records


def compute_stats(records, days=None):
    """汇总统计。"""
    if days:
        cutoff = datetime.now().astimezone() - timedelta(days=days)
        records = [r for r in records if r["timestamp"] and r["timestamp"] >= cutoff]

    total_in = sum(r["input_tokens"] for r in records)
    total_out = sum(r["output_tokens"] for r in records)
    total_cache = sum(r["cache_read_tokens"] for r in records)
    total_calls = len(records)

    by_model = defaultdict(lambda: {"input": 0, "output": 0, "cache": 0, "calls": 0})
    by_day = defaultdict(lambda: {"input": 0, "output": 0, "calls": 0})
    by_project = defaultdict(lambda: {"input": 0, "output": 0, "calls": 0})

    for r in records:
        m = r["model"]
        by_model[m]["input"] += r["input_tokens"]
        by_model[m]["output"] += r["output_tokens"]
        by_model[m]["cache"] += r["cache_read_tokens"]
        by_model[m]["calls"] += 1

        d = r["date"]
        by_day[d]["input"] += r["input_tokens"]
        by_day[d]["output"] += r["output_tokens"]
        by_day[d]["calls"] += 1

        p = r["project"]
        by_project[p]["input"] += r["input_tokens"]
        by_project[p]["output"] += r["output_tokens"]
        by_project[p]["calls"] += 1

    return {
        "records": records,
        "total_in": total_in,
        "total_out": total_out,
        "total_cache": total_cache,
        "total_calls": total_calls,
        "by_model": dict(by_model),
        "by_day": dict(by_day),
        "by_project": dict(by_project),
    }


def print_report(stats):
    """命令行报表。"""
    print("=" * 70)
    print("Claude Code Token 用量报表")
    print("=" * 70)
    print(f"统计区间: 最近 {len(stats['by_day'])} 个有数据的天数")
    print(f"总调用次数: {stats['total_calls']:,}")
    print(f"输入 tokens: {stats['total_in']:,}")
    print(f"输出 tokens: {stats['total_out']:,}")
    print(f"cache read:  {stats['total_cache']:,} (prompt cache 命中)")
    print(f"合计计费:    {stats['total_in'] + stats['total_out']:,}")
    print("-" * 70)

    # Kimi 费用估算
    k26_input = stats["total_in"] * 6.50 / 1e6
    k26_cache = stats["total_cache"] * 1.10 / 1e6
    k26_output = stats["total_out"] * 27.00 / 1e6
    k26_total = k26_input + k26_cache + k26_output
    k25_total = stats["total_in"] * 4.00 / 1e6 + stats["total_cache"] * 1.00 / 1e6 + stats["total_out"] * 16.00 / 1e6
    print(f"\n💰 Kimi 费用估算（按量）:")
    print(f"  K2.6: 输入 ¥{k26_input:,.0f} + cache ¥{k26_cache:,.0f} + 输出 ¥{k26_output:,.0f} = ¥{k26_total:,.0f}")
    print(f"  K2.5: 合计约 ¥{k25_total:,.0f}")
    print(f"  Kimi Code 订阅: ¥49~699/月（固定费用，不按量）")
    print("-" * 70)

    print("\n按模型:")
    for model, s in sorted(stats["by_model"].items(), key=lambda x: -(x[1]["input"] + x[1]["output"])):
        print(f"  {model:30s}: {s['calls']:5,} 次 | 入 {s['input']:>10,} | 出 {s['output']:>8,}")

    print("\n按项目 (top 10):")
    for proj, s in sorted(stats["by_project"].items(), key=lambda x: -(x[1]["input"] + x[1]["output"]))[:10]:
        print(f"  {proj:40s}: {s['calls']:5,} 次 | 合计 {s['input'] + s['output']:>10,}")

    print("\n按日期 (最近 14 天):")
    for d in sorted(stats["by_day"].keys())[-14:]:
        s = stats["by_day"][d]
        print(f"  {d}: {s['calls']:4,} 次 | 入 {s['input']:>9,} | 出 {s['output']:>7,} | 计 {s['input']+s['output']:>9,}")

    print("=" * 70)


def streamlit_dashboard():
    """Streamlit 可视化看板。"""
    st.set_page_config(page_title="Claude Code 用量看板", page_icon="📊", layout="wide")
    st.title("📊 Claude Code Token 用量看板")
    st.caption(f"数据来源: ~/.claude/projects/ | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    with st.spinner("扫描本地会话文件中..."):
        records = parse_all_sessions()

    if not records:
        st.error("未找到任何会话记录。确认 Claude Code 已使用过。")
        return

    # 时间范围选择
    col1, col2, col3 = st.columns(3)
    with col1:
        range_option = st.selectbox("时间范围", ["全部", "最近7天", "最近30天", "最近90天", "自定义"])
    with col2:
        start_date = st.date_input("开始日期", value=date.today() - timedelta(days=30), disabled=range_option != "自定义")
    with col3:
        end_date = st.date_input("结束日期", value=date.today(), disabled=range_option != "自定义")

    days_map = {"全部": None, "最近7天": 7, "最近30天": 30, "最近90天": 90, "自定义": None}
    days = days_map[range_option]

    if range_option == "自定义":
        records = [r for r in records if r["timestamp"]]
        records = [r for r in records if start_date <= r["timestamp"].date() <= end_date]
        stats = compute_stats(records)
    else:
        stats = compute_stats(records, days=days)

    # 顶部指标
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总调用次数", f"{stats['total_calls']:,}")
    c2.metric("输入 Tokens", f"{stats['total_in']:,.0f}")
    c3.metric("输出 Tokens", f"{stats['total_out']:,.0f}")
    c4.metric("合计计费", f"{(stats['total_in'] + stats['total_out']):,.0f}")

    st.divider()

    # 按模型饼图 + 按项目柱状图
    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("按模型分布")
        model_data = {
            k: v["input"] + v["output"]
            for k, v in sorted(stats["by_model"].items(), key=lambda x: -(x[1]["input"] + x[1]["output"]))
        }
        if model_data:
            import pandas as pd
            df_model = pd.DataFrame([
                {"模型": k, "Tokens": v} for k, v in model_data.items()
            ])
            st.bar_chart(df_model.set_index("模型"))

    with col_right:
        st.subheader("按项目分布 (Top 10)")
        import pandas as pd
        proj_data = sorted(stats["by_project"].items(), key=lambda x: -(x[1]["input"] + x[1]["output"]))[:10]
        df_proj = pd.DataFrame([
            {"项目": k, "Tokens": v["input"] + v["output"]} for k, v in proj_data
        ])
        st.bar_chart(df_proj.set_index("项目"))

    st.divider()

    # 每日趋势
    st.subheader("每日 Token 趋势")
    import pandas as pd
    day_data = sorted(stats["by_day"].items())
    df_day = pd.DataFrame([
        {"日期": d, "输入": s["input"], "输出": s["output"], "合计": s["input"] + s["output"]} for d, s in day_data
    ])
    if not df_day.empty:
        df_day["日期"] = pd.to_datetime(df_day["日期"])
        df_day = df_day.sort_values("日期").set_index("日期")
        st.line_chart(df_day[["输入", "输出", "合计"]])

    st.divider()

    # 详细表格
    st.subheader("按日期明细")
    df_detail = pd.DataFrame([
        {"日期": d, "调用次数": s["calls"], "输入": s["input"], "输出": s["output"], "合计": s["input"] + s["output"]}
        for d, s in sorted(stats["by_day"].items(), reverse=True)
    ])
    st.dataframe(df_detail, use_container_width=True)

    # 费用估算
    st.divider()
    st.subheader("💰 费用估算（Kimi）")

    # Kimi K2.6 按量计费（2026-05 价目）
    k26_input_cost = stats["total_in"] * 6.50 / 1e6
    k26_cache_cost = stats["total_cache"] * 1.10 / 1e6
    k26_output_cost = stats["total_out"] * 27.00 / 1e6
    k26_total = k26_input_cost + k26_cache_cost + k26_output_cost

    # Kimi K2.5 按量计费（旧型号，逐步下线）
    k25_input_cost = stats["total_in"] * 4.00 / 1e6
    k25_cache_cost = stats["total_cache"] * 1.00 / 1e6
    k25_output_cost = stats["total_out"] * 16.00 / 1e6
    k25_total = k25_input_cost + k25_cache_cost + k25_output_cost

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Kimi K2.6 按量", f"¥{k26_total:,.0f}", help=f"输入 ¥6.5/M + cache ¥1.1/M + 输出 ¥27/M | cache 命中 {(stats['total_cache']/1e6):,.0f}M")
    col_b.metric("Kimi K2.5 按量", f"¥{k25_total:,.0f}", help=f"输入 ¥4/M + cache ¥1/M + 输出 ¥16/M")
    col_c.metric("Kimi Code 订阅", "¥49~699/月", help="Andante ¥49 | Moderato ¥99 | Allegretto ¥199 | Allegro ¥699")

    st.info("💡 你当前模型为 `kimi-for-coding`。如果是 **Kimi Code 订阅**（支持 Claude Code 接入），费用为固定月费，不按 token 计费。如果是 **按量调用 Kimi API**，则参考左侧两列估算。cache_read 为 prompt cache 命中，价格远低于正常输入。")


def main():
    if HAS_STREAMLIT and st is not None and hasattr(st, "set_page_config"):
        # 在 streamlit 环境中运行
        streamlit_dashboard()
    else:
        # 命令行模式
        print("正在扫描 ~/.claude/projects/ ...")
        records = parse_all_sessions()
        if not records:
            print("未找到会话记录。")
            sys.exit(1)

        # 默认最近30天，如果没有则全部
        stats = compute_stats(records, days=30)
        # 如果30天数据与全部相同，只打一份
        stats_all = compute_stats(records)
        if stats["total_calls"] == stats_all["total_calls"]:
            print_report(stats)
        else:
            print("【最近30天】")
            print_report(stats)
            print("\n【全部历史】")
            print_report(stats_all)


if __name__ == "__main__":
    main()
