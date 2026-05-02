"""
LLM Usage Dashboard
====================
Streamlit dashboard for visualizing WorkBuddy token usage and costs.

启动: python -m streamlit run dashboard.py
"""

import sys
from pathlib import Path

import duckdb
import streamlit as st

# ============================================================
# 页面配置
# ============================================================

st.set_page_config(
    page_title="LLM Usage Monitor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = Path(__file__).parent / "llm_usage.duckdb"

# ============================================================
# 数据加载
# ============================================================

@st.cache_resource
def get_connection():
    return duckdb.connect(str(DB_PATH), read_only=True)


def query(sql: str) -> duckdb.DuckDBPyRelation:
    con = get_connection()
    return con.execute(sql).fetchdf()


# ============================================================
# 侧边栏 - 筛选条件
# ============================================================

st.sidebar.title("📊 LLM Monitor")

# 检查数据库
if not DB_PATH.exists():
    st.error("❌ 数据库不存在，请先运行 `python llm_monitor.py --import`")
    st.stop()

# 加载日期范围
dates_df = query("SELECT MIN(start_date) as min_d, MAX(start_date) as max_d FROM llm_usage WHERE start_date IS NOT NULL")
min_date = dates_df["min_d"].iloc[0]
max_date = dates_df["max_d"].iloc[0]

# 加载模型列表
models_df = query("SELECT DISTINCT primary_model FROM llm_usage WHERE primary_model != 'unknown' ORDER BY primary_model")
all_models = models_df["primary_model"].tolist()

# 日期筛选
if min_date is not None and max_date is not None:
    date_range = st.sidebar.date_input(
        "📅 日期范围",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
else:
    date_range = None

# 模型筛选
selected_models = st.sidebar.multiselect(
    "🤖 模型筛选",
    options=all_models,
    default=all_models,
)

# 构建 WHERE 条件
conditions = ["1=1"]
if date_range and len(date_range) == 2:
    conditions.append(f"start_date >= '{date_range[0]}'")
    conditions.append(f"start_date <= '{date_range[1]}'")
if selected_models:
    model_list = ", ".join(f"'{m}'" for m in selected_models)
    conditions.append(f"primary_model IN ({model_list})")

where_clause = " AND ".join(conditions)

# ============================================================
# 主页面
# ============================================================

st.title("📊 LLM Token & Cost Monitor")
st.caption("WorkBuddy 大模型使用统计")

# ---- 总体概览 ----
overview = query(f"""
    SELECT
        COUNT(*) as total_sessions,
        SUM(call_count) as total_calls,
        SUM(input_tokens) as total_input,
        SUM(output_tokens) as total_output,
        SUM(cached_tokens) as total_cached,
        SUM(total_cost_usd) as total_cost,
        SUM(duration_ms) / 1000.0 as total_seconds
    FROM llm_usage
    WHERE {where_clause}
""")

col1, col2, col3, col4 = st.columns(4)
col1.metric("会话数", f"{int(overview['total_sessions'].iloc[0]):,}")
col2.metric("API调用", f"{int(overview['total_calls'].iloc[0]):,}")

total_input = int(overview['total_input'].iloc[0])
total_cached = int(overview['total_cached'].iloc[0])
cache_rate = (total_cached / total_input * 100) if total_input > 0 else 0
col3.metric("输入 Tokens", f"{total_input:,}", help=f"缓存命中率: {cache_rate:.1f}%")
col4.metric("总成本", f"${overview['total_cost'].iloc[0]:,.4f}")

col5, col6, col7, col8 = st.columns(4)
col5.metric("输出 Tokens", f"{int(overview['total_output'].iloc[0]):,}")
col6.metric("缓存 Tokens", f"{total_cached:,}", help=f"命中率 {cache_rate:.1f}%")
total_sec = float(overview['total_seconds'].iloc[0])
hours = int(total_sec // 3600)
mins = int((total_sec % 3600) // 60)
col7.metric("总耗时", f"{hours}h {mins}m")
col8.metric("平均成本/会话", f"${overview['total_cost'].iloc[0] / max(1, int(overview['total_sessions'].iloc[0])):.4f}")

st.divider()

# ---- 每日趋势图 ----
st.subheader("📈 每日消耗趋势")

daily_df = query(f"""
    SELECT
        start_date as "日期",
        SUM(input_tokens) as "输入Tokens",
        SUM(output_tokens) as "输出Tokens",
        SUM(total_cost_usd) as "成本($)",
        COUNT(*) as "会话数"
    FROM llm_usage
    WHERE {where_clause}
    GROUP BY start_date
    ORDER BY start_date
""")

if not daily_df.empty:
    col_chart1, col_chart2 = st.columns(2)
    with col_chart1:
        st.bar_chart(daily_df, x="日期", y=["输入Tokens", "输出Tokens"], height=300)
    with col_chart2:
        st.line_chart(daily_df, x="日期", y=["成本($)"], height=300)

    with st.expander("📋 每日数据明细"):
        st.dataframe(daily_df, use_container_width=True)
else:
    st.info("无数据")

st.divider()

# ---- 模型分布 ----
st.subheader("🤖 模型使用分布")

model_df = query(f"""
    SELECT
        primary_model as "模型",
        COUNT(*) as "会话数",
        SUM(input_tokens) as "输入Tokens",
        SUM(output_tokens) as "输出Tokens",
        ROUND(SUM(total_cost_usd), 4) as "成本($)",
        ROUND(SUM(total_cost_usd) / NULLIF(SUM(input_tokens + output_tokens), 0) * 1000000, 2) as "每百万token成本($)"
    FROM llm_usage
    WHERE {where_clause}
    GROUP BY primary_model
    ORDER BY "成本($)" DESC
""")

if not model_df.empty:
    col_model1, col_model2 = st.columns(2)
    with col_model1:
        st.bar_chart(model_df, x="模型", y="成本($)", height=300)
    with col_model2:
        st.bar_chart(model_df, x="模型", y="会话数", height=300)

    with st.expander("📋 模型数据明细"):
        st.dataframe(model_df, use_container_width=True)

st.divider()

# ---- 会话排行 ----
st.subheader("🏆 Top 会话消耗")

session_df = query(f"""
    SELECT
        trace_id as "Trace ID",
        session_id as "Session",
        primary_model as "模型",
        started_at as "开始时间",
        input_tokens as "输入Tokens",
        output_tokens as "输出Tokens",
        call_count as "调用次数",
        ROUND(duration_ms / 1000.0, 1) as "耗时(秒)",
        ROUND(total_cost_usd, 4) as "成本($)"
    FROM llm_usage
    WHERE {where_clause}
    ORDER BY total_cost_usd DESC
    LIMIT 20
""")

if not session_df.empty:
    st.dataframe(session_df, use_container_width=True)

st.divider()

# ---- 缓存效率 ----
st.subheader("💾 缓存效率分析")

cache_df = query(f"""
    SELECT
        primary_model as "模型",
        SUM(input_tokens) as "总输入",
        SUM(cached_tokens) as "缓存命中",
        ROUND(SUM(cached_tokens) * 100.0 / NULLIF(SUM(input_tokens), 0), 1) as "命中率(%)",
        ROUND(SUM(cached_tokens) / 1000000.0 * 0.3, 4) as "缓存节省估算($)"
    FROM llm_usage
    WHERE {where_clause}
    GROUP BY primary_model
    HAVING SUM(input_tokens) > 0
    ORDER BY "命中率(%)" DESC
""")

if not cache_df.empty:
    st.dataframe(cache_df, use_container_width=True)

# ============================================================
# 底部
# ============================================================

st.divider()
st.caption("数据来源: WorkBuddy traces/ | 自动更新请运行 `python llm_monitor.py --import`")
