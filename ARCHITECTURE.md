# App Store 竞品洞察 Agent — 项目结构文档

GitHub: https://github.com/qinghaoz-cell/appstore-intelligence-agent  
部署: https://appstore-agent.streamlit.app

---

## 文件结构

```
appstore-intelligence-agent/
├── agent.py       # 核心逻辑：评论分析 + 竞品洞察 Agent + PRD 生成
├── app.py         # Streamlit 前端：输入表单 + 结果展示
├── scraper.py     # 数据抓取：iTunes RSS Feed + Tavily 兜底
├── requirements.txt
└── .env           # 本地密钥（不上传）
```

---

## 核心数据流

```
用户输入（主产品 + 竞品名）
  ↓
scraper.search_app()        → iTunes API 搜索，返回 app_id
  ↓
scraper.get_reviews()       → iTunes RSS 抓评论，失败则 Tavily 搜索兜底
  ↓
agent._analyze_app()        → 单次 Claude 调用，返回结构化分析 JSON
  ↓  （每完成一个 App 立即回调 on_app_analysis 展示卡片）
agent._generate_insights()  → 研究 Agent 循环：判断证据缺口，自主核查原始评论或搜索产品动态
  ↓
agent.stream_prd_draft()    → 用户选机会点后，流式生成 PRD Markdown
```

### 双语展示与输出

- `app.py` 顶部提供“语言 / Language”切换，默认中文；英文模式默认选择美国 App Store。
- 语言选择会传给 `run_agent(..., language=...)`：英文模式不仅翻译界面，也要求评论洞察、研究轨迹和 PRD 草稿全部用英文输出。
- 切换语言后需要重新运行分析，避免用一种语言的结果搭配另一种语言的页面标签。

---

## agent.py 关键函数

| 函数 | 位置 | 作用 |
|------|------|------|
| `_extract_json(text)` | ~81行 | 从 Claude 输出里提取 JSON 字符串，去掉 markdown 代码块 |
| `_parse_json(text)` | ~105行 | 解析 JSON，失败则调 Claude 修复；失败时抛出可见错误 |
| `_clean(text)` | ~114行 | 清理评论特殊字符，防止破坏 JSON |
| `_analyze_app(app_name, reviews)` | ~119行 | 分析单个 App 评论，返回痛点/好评/需求/总结 |
| `_review_evidence(review_evidence, app_name, focus)` | ~195行 | 按 Agent 请求返回指定 App 的原始评论节选 |
| `_generate_insights(app_analyses, main_app, review_evidence)` | ~205行 | **竞品研究 Agent 循环**：自主决定核查评论或搜索，最多 5 次研究动作 |
| `run_agent(main_app, competitors, ...)` | ~225行 | 主入口，串联整个流程，支持 on_app_analysis 回调 |
| `stream_prd_draft(opportunity, ...)` | ~289行 | 流式生成 PRD，用 yield 逐 chunk 返回 |

---

## _analyze_app 输出的 JSON 结构

```json
{
  "top_pain_points": [
    {"issue": "痛点描述", "frequency": "high/medium/low", "example_quote": "「原文」"}
  ],
  "top_positives": [
    {"strength": "好评描述", "frequency": "high/medium/low", "example_quote": "「原文」"}
  ],
  "overall_sentiment": "positive/mixed/negative",
  "key_feature_requests": ["需求1", "需求2", "需求3"],
  "summary": "2-3句总结"
}
```

---

## _generate_insights 输出的 JSON 结构

```json
{
  "must_close_gaps": [{"gap": "...", "competitor": "...", "urgency": "high/medium", "evidence": "..."}],
  "opportunity_windows": [{"opportunity": "...", "rationale": "...", "evidence": "..."}],
  "core_advantages": [{"advantage": "...", "how_to_amplify": "...", "evidence": "..."}],
  "priority_matrix": [{"action": "...", "impact": "high/medium/low", "effort": "high/medium/low"}],
  "positioning_recommendation": "差异化定位建议",
  "summary": "战略总结",
  "research_assessment": {"confidence": "high/medium/low", "coverage": "...", "remaining_uncertainty": "..."},
  "research_trace": [{"action": "...", "target": "...", "reason": "..."}]
}
```

### 研究 Agent 的工具与边界

- `inspect_review_evidence`：当评论摘要不足以支撑结论时，读取指定 App 的原始评论节选。
- `web_search`：仅在评论无法覆盖近期功能或市场变化时，搜索公开信息；没有 Tavily API Key 时不会启用。
- `MAX_RESEARCH_ACTIONS = 5`：限制一次研究的工具调用总数，控制耗时与成本。
- 最终结论要求标注证据、置信度和待验证项；前端会展示 Agent 的补充研究轨迹。

---

## scraper.py 逻辑

```
get_reviews(app_name, app_id, country, count, language)
  ├── _get_rss_reviews()   # iTunes RSS Feed，免费无需认证，但经常返回空
  └── _get_tavily_reviews() # RSS 为空时兜底；按语言搜索中文或英文用户评价
```

**注意：** iTunes RSS Feed 目前经常失效，大部分数据来自 Tavily 搜索。

---

## app.py 关键变量

| 变量/函数 | 作用 |
|-----------|------|
| `main_app` | 用户输入的主产品名 |
| `competitors` | 竞品列表（逗号分隔后 split） |
| `country` | App Store 地区，目前只有 cn / us |
| `show_app_card(app_name, analysis)` | 渲染单个 App 分析卡片 |
| `step1_container` | 用于流式展示卡片的 Streamlit 容器 |
| `on_app_analysis(app_name, analysis)` | 每个 App 分析完立即触发，写入 step1_container |
| `st.session_state["app_analyses"]` | 所有 App 分析结果，run 结束后持久化 |
| `st.session_state["insights"]` | 竞品洞察结果 |
| `st.session_state["main_app_name"]` | 主产品名，用于标题展示 |

---

## 常见修改任务

### 修改分析维度（痛点/好评条数、新增字段）
1. 改 `agent.py` → `_analyze_app()` 里的 prompt（~125行）
2. 同步改 JSON 结构里的字段名
3. 改 `app.py` → `show_app_card()` 里对应字段的展示逻辑

### 修改竞品洞察的输出内容
1. 改 `agent.py` → `_generate_insights()` 里的 system prompt（~155行）
2. 同步改 JSON 结构
3. 改 `app.py` → Section 2 的展示逻辑（~93行起）

### 修改 PRD 模板
1. 改 `agent.py` → `PRD_GENERATION_PROMPT`（~39行）
2. 格式是 Markdown，直接改模板文本即可

### 新增 App Store 地区
1. 改 `app.py` → `st.selectbox("App Store 地区", ["cn", "us"], ...)` 加选项
2. 确认 iTunes RSS Feed 支持该地区代码

### 修改 web_search 行为（Agent 决策）
1. 改 `agent.py` → `_generate_insights()` 里的 system prompt
2. 关键约束在这句：`按需，最多 2 次`，改数字即可控制调用频率

### 换模型
1. 全局搜索 `claude-sonnet-4-6`，替换为目标模型名
2. 注意 `_parse_json` 里的修复调用也要同步改

---

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `ANTHROPIC_API_KEY` | 是 | Claude API 密钥 |
| `TAVILY_API_KEY` | 否 | 有则启用 web_search 和评论兜底，无则跳过 |

本地：写在 `.env` 文件  
Streamlit Cloud：Settings → Secrets

---

## 部署流程

```bash
# 本地运行
pip install -r requirements.txt
streamlit run app.py

# 推送到 GitHub 后 Streamlit Cloud 自动重新部署
git add .
git commit -m "说明改了什么"
git push origin main
```

Streamlit Cloud 项目地址：https://share.streamlit.io（登录后查看）
