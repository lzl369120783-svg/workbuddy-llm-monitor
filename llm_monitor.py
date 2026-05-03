"""
LLM Token & Cost Monitor for WorkBuddy
========================================
解析 WorkBuddy traces/ 目录下的 JSON 文件，
提取 token 使用数据并导入 DuckDB 进行统计分析。

用法:
    python llm_monitor.py              # 解析所有traces
    python llm_monitor.py --import     # 解析并导入DuckDB
    python llm_monitor.py --stats      # 输出统计摘要
    python llm_monitor.py --dashboard  # 启动Streamlit dashboard
"""

import json
import os
import glob
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

# ============================================================
# 配置
# ============================================================

# WorkBuddy traces 目录（默认在用户目录下）
TRACES_DIR = Path.home() / ".workbuddy" / "traces"

# DuckDB 数据库文件
DB_PATH = Path(__file__).parent / "llm_usage.duckdb"

# 端点ID -> 真实模型名的映射
# NVIDIA 等平台用端点ID(ep-xxx)记录模型，traces里只有端点ID
MODEL_ALIASES = {
    "ep-i72eb58u": "zhipu/glm5.1",
}

# WorkBuddy models.json 路径（用于自动发现端点映射）
MODELS_JSON = Path.home() / ".workbuddy" / "models.json"

# 模型定价表 (USD per 1M tokens)
# 格式: model_name -> { input, output, cache_read }
MODEL_PRICING = {
    # Claude 系列 (2026年4月价格)
    "auto-pro":       {"input": 0.0,   "output": 0.0,   "cache_read": 0.0},   # 实际=腾讯Hy3 Preview免费
    "auto":           {"input": 0.0,   "output": 0.0,   "cache_read": 0.0},   # 实际=腾讯Hy3 Preview免费
    "claude-4-opus":  {"input": 5.0,   "output": 25.0,  "cache_read": 0.5},
    "claude-4-sonnet":{"input": 3.0,   "output": 15.0,  "cache_read": 0.3},
    "claude-4-haiku": {"input": 1.0,   "output": 5.0,   "cache_read": 0.1},   # Claude Haiku 4.5
    # DeepSeek
    "deepseek-ai/deepseek-v3.2":  {"input": 0.27,  "output": 1.10, "cache_read": 0.07},
    "deepseek-ai/deepseek-v4-flash": {"input": 0.14, "output": 0.28, "cache_read": 0.014},
    "deepseek-ai/deepseek-v4-pro":   {"input": 0.55, "output": 2.19, "cache_read": 0.055},
    "deepseek-chat":  {"input": 0.14,  "output": 0.28,  "cache_read": 0.014},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19, "cache_read": 0.055},
    # 智谱 GLM
    "zhipu/glm5.1":   {"input": 0.14,  "output": 0.14,  "cache_read": 0.014},
    "zhipu/glm5":     {"input": 0.14,  "output": 0.14,  "cache_read": 0.014},
    "z-ai/glm5":      {"input": 0.14,  "output": 0.14,  "cache_read": 0.014},
    # Qwen
    "qwen/qwen3.5-397b-a17b": {"input": 0.27, "output": 1.10, "cache_read": 0.07},
    "qwen3.5:4b":     {"input": 0.0,   "output": 0.0,   "cache_read": 0.0},   # 本地Ollama
    "qwen3.5:latest":  {"input": 0.0,   "output": 0.0,   "cache_read": 0.0},   # 本地Ollama
    # MiMo
    "mimo-v2.5":      {"input": 0.14,  "output": 0.56,  "cache_read": 0.014},
    "mimo-v2.5-pro":  {"input": 0.27,  "output": 1.10,  "cache_read": 0.027},
    "mimo-v2.5-flash":{"input": 0.0,   "output": 0.0,   "cache_read": 0.0},
    # MiniMax
    "minimaxai/minimax-m2.7": {"input": 0.27, "output": 1.10, "cache_read": 0.07},
    # Kimi
    "kimi-k2.6":      {"input": 0.27,  "output": 1.10,  "cache_read": 0.07},
    # Google Gemma (本地)
    "gemma4:e2b":     {"input": 0.0,   "output": 0.0,   "cache_read": 0.0},
    # Tencent Hy3 preview
    "hy3-preview-agent": {"input": 0.17, "output": 0.56, "cache_read": 0.06},  # 腾讯云 ¥1.2/¥4 per Mtok ≈ $0.17/$0.56
    "tencent/hy3-preview-20260421:free": {"input": 0.0, "output": 0.0, "cache_read": 0.0},  # OpenRouter免费版(5月8日下线)
    "tencent/hy3-preview": {"input": 0.17, "output": 0.56, "cache_read": 0.06},
    # 未知模型默认
    "_default":       {"input": 3.0,   "output": 15.0,  "cache_read": 0.3},
}


# ============================================================
# Trace 解析
# ============================================================

def parse_trace_file(filepath: str) -> dict | None:
    """解析单个 trace JSON 文件"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        trace = data.get("trace", {})
        model_info = trace.get("modelInfo", {})

        if not trace.get("traceId"):
            return None

        # 提取时间
        started_at = trace.get("startedAt", "")
        ended_at = trace.get("endedAt", "")

        # 解析日期
        start_date = None
        if started_at:
            try:
                dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                start_date = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        # 模型列表
        models = model_info.get("models", [])
        primary_model = models[0] if models else "unknown"

        return {
            "trace_id": trace.get("traceId", ""),
            "session_id": trace.get("sessionId", ""),
            "agent_name": trace.get("agentName", ""),
            "started_at": started_at,
            "ended_at": ended_at,
            "start_date": start_date,
            "duration_ms": trace.get("duration", 0),
            "status": trace.get("status", ""),
            "span_count": trace.get("spanCount", 0),
            "primary_model": MODEL_ALIASES.get(primary_model, primary_model),
            "all_models": [MODEL_ALIASES.get(m, m) for m in models],
            "input_tokens": model_info.get("totalInputTokens", 0),
            "output_tokens": model_info.get("totalOutputTokens", 0),
            "cached_tokens": model_info.get("totalCachedTokens", 0),
            "call_count": model_info.get("callCount", 0),
            "source_file": str(filepath),
        }
    except Exception as e:
        print(f"  [WARN] 解析失败 {filepath}: {e}")
        return None


def scan_all_traces(traces_dir: Path = TRACES_DIR) -> list[dict]:
    """扫描并解析所有 trace 文件"""
    records = []
    pattern = str(traces_dir / "**" / "*.json")
    files = glob.glob(pattern, recursive=True)

    print(f"📂 扫描 {traces_dir} ...")
    print(f"   找到 {len(files)} 个 trace 文件")

    for f in sorted(files):
        rec = parse_trace_file(f)
        if rec:
            records.append(rec)

    print(f"✅ 成功解析 {len(records)} 条记录")
    return records


# ============================================================
# 成本计算
# ============================================================

def calc_cost(input_tokens: int, output_tokens: int, cached_tokens: int,
              model: str, pricing: dict = MODEL_PRICING) -> dict:
    """计算单次调用的成本 (USD)"""
    p = pricing.get(model, pricing["_default"])

    input_cost = (input_tokens / 1_000_000) * p["input"]
    output_cost = (output_tokens / 1_000_000) * p["output"]
    # 缓存命中部分享受 cache_read 价格（替代 input 价格）
    # 未缓存部分仍按 input 价格
    non_cached = max(0, input_tokens - cached_tokens)
    cache_cost = (cached_tokens / 1_000_000) * p["cache_read"]
    non_cached_cost = (non_cached / 1_000_000) * p["input"]
    input_total = non_cached_cost + cache_cost

    total = input_total + output_cost

    return {
        "input_cost_usd": round(input_total, 6),
        "output_cost_usd": round(output_cost, 6),
        "total_cost_usd": round(total, 6),
        "price_model": model,
    }


def enrich_with_cost(records: list[dict]) -> list[dict]:
    """为每条记录添加成本数据"""
    for rec in records:
        cost = calc_cost(
            rec["input_tokens"],
            rec["output_tokens"],
            rec["cached_tokens"],
            rec["primary_model"],
        )
        rec.update(cost)
    return records


# ============================================================
# DuckDB 存储
# ============================================================

def init_db(db_path: Path = DB_PATH, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """初始化数据库"""
    con = duckdb.connect(str(db_path), read_only=read_only)

    if not read_only:
        con.execute("""
            CREATE TABLE IF NOT EXISTS llm_usage (
                trace_id       VARCHAR PRIMARY KEY,
                session_id     VARCHAR,
                agent_name     VARCHAR,
                started_at     VARCHAR,
                ended_at       VARCHAR,
                start_date     DATE,
                duration_ms    BIGINT,
                status         VARCHAR,
                span_count     INTEGER,
                primary_model  VARCHAR,
                all_models     VARCHAR,
                input_tokens   BIGINT,
                output_tokens  BIGINT,
                cached_tokens  BIGINT,
                call_count     INTEGER,
                input_cost_usd DOUBLE,
                output_cost_usd DOUBLE,
                total_cost_usd DOUBLE,
                price_model    VARCHAR,
                source_file    VARCHAR
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                model_name     VARCHAR PRIMARY KEY,
                input_per_mtok  DOUBLE,
                output_per_mtok DOUBLE,
                cache_read_per_mtok DOUBLE,
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    return con


def import_records(con: duckdb.DuckDBPyConnection, records: list[dict]) -> int:
    """导入记录到数据库"""
    if not records:
        print("⚠️  没有记录可导入")
        return 0

    # 准备数据
    rows = []
    for rec in records:
        rows.append((
            rec["trace_id"],
            rec["session_id"],
            rec["agent_name"],
            rec["started_at"],
            rec["ended_at"],
            rec["start_date"],
            rec["duration_ms"],
            rec["status"],
            rec["span_count"],
            rec["primary_model"],
            ",".join(rec["all_models"]),
            rec["input_tokens"],
            rec["output_tokens"],
            rec["cached_tokens"],
            rec["call_count"],
            rec["input_cost_usd"],
            rec["output_cost_usd"],
            rec["total_cost_usd"],
            rec["price_model"],
            rec["source_file"],
        ))

    # 获取已有的 trace_id
    existing = set()
    try:
        result = con.execute("SELECT trace_id FROM llm_usage").fetchall()
        existing = {row[0] for row in result}
    except Exception:
        pass

    # 过滤已有记录
    new_rows = [r for r in rows if r[0] not in existing]

    if not new_rows:
        print(f"ℹ️  所有 {len(rows)} 条记录已存在，无需导入")
        return 0

    con.executemany("""
        INSERT INTO llm_usage (
            trace_id, session_id, agent_name, started_at, ended_at,
            start_date, duration_ms, status, span_count, primary_model,
            all_models, input_tokens, output_tokens, cached_tokens, call_count,
            input_cost_usd, output_cost_usd, total_cost_usd, price_model, source_file
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, new_rows)

    print(f"✅ 导入 {len(new_rows)} 条新记录 (跳过 {len(rows) - len(new_rows)} 条已有)")
    return len(new_rows)


# ============================================================
# 统计查询
# ============================================================

def print_stats(con: duckdb.DuckDBPyConnection):
    """打印统计摘要"""
    print("\n" + "=" * 60)
    print("📊 LLM 使用统计")
    print("=" * 60)

    # 总体统计
    row = con.execute("""
        SELECT
            COUNT(*) as sessions,
            SUM(input_tokens) as total_input,
            SUM(output_tokens) as total_output,
            SUM(cached_tokens) as total_cached,
            SUM(call_count) as total_calls,
            SUM(total_cost_usd) as total_cost,
            SUM(duration_ms) / 1000.0 as total_seconds,
            MIN(start_date) as first_date,
            MAX(start_date) as last_date
        FROM llm_usage
    """).fetchone()

    print(f"\n📈 总体概况")
    print(f"  会话数:     {row[0]:,}")
    print(f"  API调用:    {row[4]:,} 次")
    print(f"  输入tokens: {row[1]:,}")
    print(f"  输出tokens: {row[2]:,}")
    print(f"  缓存tokens: {row[3]:,}")
    cache_rate = (row[3] / row[1] * 100) if row[1] > 0 else 0
    print(f"  缓存命中率: {cache_rate:.1f}%")
    print(f"  总成本:     ${row[5]:,.4f}" if row[5] else "  总成本:     $0.00")
    print(f"  总耗时:     {row[6]:,.1f}秒" if row[6] else "  总耗时:     0秒")
    print(f"  时间范围:   {row[7]} ~ {row[8]}")

    # 按模型统计
    print(f"\n🤖 按模型统计")
    rows = con.execute("""
        SELECT
            primary_model,
            COUNT(*) as sessions,
            SUM(input_tokens) as input,
            SUM(output_tokens) as output,
            SUM(total_cost_usd) as cost
        FROM llm_usage
        GROUP BY primary_model
        ORDER BY cost DESC
    """).fetchall()

    print(f"  {'模型':<20} {'会话':>6} {'输入tokens':>14} {'输出tokens':>14} {'成本':>12}")
    print(f"  {'-'*20} {'-'*6} {'-'*14} {'-'*14} {'-'*12}")
    for r in rows:
        print(f"  {r[0]:<20} {r[1]:>6,} {r[2]:>14,} {r[3]:>14,} ${r[4]:>10,.4f}" if r[4] else
              f"  {r[0]:<20} {r[1]:>6,} {r[2]:>14,} {r[3]:>14,} $0.0000")

    # 按日期统计 (最近7天)
    print(f"\n📅 最近7天")
    rows = con.execute("""
        SELECT
            start_date,
            COUNT(*) as sessions,
            SUM(input_tokens) as input,
            SUM(output_tokens) as output,
            SUM(total_cost_usd) as cost
        FROM llm_usage
        WHERE start_date >= CURRENT_DATE - INTERVAL '7 days'
        GROUP BY start_date
        ORDER BY start_date DESC
    """).fetchall()

    if rows:
        print(f"  {'日期':<12} {'会话':>6} {'输入tokens':>14} {'输出tokens':>14} {'成本':>12}")
        print(f"  {'-'*12} {'-'*6} {'-'*14} {'-'*14} {'-'*12}")
        for r in rows:
            d = str(r[0])[:10] if r[0] else "N/A"
            cost_val = r[4] if r[4] else 0
            print(f"  {d:<12} {r[1]:>6,} {r[2]:>14,} {r[3]:>14,} ${cost_val:>10,.4f}")
    else:
        print("  (无数据)")

    print("\n" + "=" * 60)


# ============================================================
# CLI 入口
# ============================================================

def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--stats" in args and "--import" not in args:
        # 只查库，不需要重新扫描traces
        if not DB_PATH.exists():
            print("❌ 数据库不存在，请先运行 --import")
            return
        con = init_db(read_only=True)
        print_stats(con)
        con.close()
        return

    # 需要扫描traces
    records = scan_all_traces()
    if not records:
        print("❌ 未找到任何 trace 数据")
        return

    records = enrich_with_cost(records)

    if "--import" in args:
        con = init_db()
        import_records(con, records)
        if "--stats" in args:
            print_stats(con)
        con.close()

    elif "--dashboard" in args:
        if not DB_PATH.exists():
            con = init_db()
            import_records(con, records)
            con.close()
        print("\n🚀 启动 Streamlit Dashboard...")
        import subprocess
        dashboard_path = Path(__file__).parent / "dashboard.py"
        subprocess.run([
            sys.executable, "-m", "streamlit", "run",
            str(dashboard_path),
            "--server.headless", "true",
            "--server.port", "8501",
        ])
    else:
        # 默认只输出摘要
        print(f"\n📊 扫描到 {len(records)} 条记录")
        total_input = sum(r["input_tokens"] for r in records)
        total_output = sum(r["output_tokens"] for r in records)
        total_cost = sum(r["total_cost_usd"] for r in records)
        print(f"  输入tokens: {total_input:,}")
        print(f"  输出tokens: {total_output:,}")
        print(f"  总成本:     ${total_cost:,.4f}")
        print(f"\n💡 使用 --import 导入DuckDB, --stats 查看详细统计, --dashboard 启动可视化")


if __name__ == "__main__":
    main()
