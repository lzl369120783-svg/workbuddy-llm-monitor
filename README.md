# WorkBuddy LLM Monitor

📊 WorkBuddy 大模型 Token 消耗与成本监控工具

## 功能

- **Trace 解析**：自动扫描 WorkBuddy `traces/` 目录，提取每次会话的 token 使用数据
- **成本计算**：基于模型定价表计算每次 API 调用的成本，支持缓存折扣
- **DuckDB 存储**：高性能本地数据库，支持增量导入
- **CLI 统计**：终端查看每日/模型维度的使用统计
- **Streamlit Dashboard**：可视化 Web 界面，支持日期/模型筛选

## 快速开始

### 安装依赖

```bash
pip install duckdb streamlit
```

### 导入数据

```bash
python llm_monitor.py --import --stats
```

### 启动 Dashboard

```bash
python llm_monitor.py --dashboard
```

然后访问 http://localhost:8501

## 命令行用法

```bash
# 扫描 traces 并输出摘要
python llm_monitor.py

# 导入 DuckDB 并查看统计
python llm_monitor.py --import --stats

# 启动 Streamlit 可视化
python llm_monitor.py --dashboard
```

## 支持的模型

| 模型 | 定价 (USD/1M tokens) |
|------|---------------------|
| Claude 4 Opus | $15 input / $75 output |
| Claude 4 Sonnet | $3 input / $15 output |
| Claude 4 Haiku | $0.25 input / $1.25 output |
| DeepSeek V3.2 | $0.27 input / $1.10 output |
| DeepSeek V4 Flash | $0.14 input / $0.28 output |
| 智谱 GLM5.1 | $0.14 input / $0.14 output |
| Qwen 3.5 | $0.27 input / $1.10 output |
| MiMo V2.5 | $0.14 input / $0.56 output |
| MiniMax M2.7 | $0.27 input / $1.10 output |
| Kimi K2.6 | $0.27 input / $1.10 output |
| 本地模型 (Ollama) | $0 |

### 添加自定义模型

编辑 `llm_monitor.py` 中的 `MODEL_PRICING` 字典：

```python
MODEL_PRICING = {
    "your-model-name": {"input": 0.5, "output": 1.0, "cache_read": 0.05},
}
```

### 端点映射

如果 traces 记录的是端点 ID 而非模型名，在 `MODEL_ALIASES` 中添加映射：

```python
MODEL_ALIASES = {
    "ep-xxxx": "zhipu/glm5.1",
}
```

## 文件结构

```
workbuddy-llm-monitor/
├── README.md          # 项目说明
├── LICENSE            # MIT 许可证
├── llm_monitor.py     # 核心：数据解析 + DuckDB 导入 + CLI
├── dashboard.py       # Streamlit 可视化 Dashboard
└── .gitignore         # 忽略数据库和缓存文件
```

## Dashboard 功能

- 📈 **每日消耗趋势**：输入/输出 token 和成本的柱状图 + 折线图
- 🤖 **模型分布**：各模型的使用次数和成本对比
- 🏆 **Top 会话消耗**：消耗最高的 20 个会话
- 💾 **缓存效率分析**：各模型的缓存命中率和节省估算
- 🔍 **筛选功能**：按日期范围和模型类型筛选

## 环境要求

- Python 3.10+
- DuckDB
- Streamlit

## License

MIT
