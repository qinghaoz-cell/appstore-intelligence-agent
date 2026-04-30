import streamlit as st
from scraper import search_app, get_reviews
from agent import analyze_app_reviews, generate_competitive_insights, stream_prd_draft

st.set_page_config(page_title="App Store Intelligence Agent", page_icon="🔍", layout="wide")

st.title("🔍 App Store Intelligence Agent")
st.caption("输入 App 名称，自动抓取评论并分析用户痛点、竞品差距，一键生成需求草稿")

# ── Input ──────────────────────────────────────────────────────────────────
with st.form("input_form"):
    col1, col2 = st.columns([3, 1])
    with col1:
        main_app = st.text_input("主产品名称", placeholder="例如：小红书")
    with col2:
        country = st.selectbox("App Store 地区", ["cn", "us", "hk", "tw"], index=0)
    competitor_input = st.text_input("竞品名称（可选，英文逗号分隔）", placeholder="例如：抖音, 微博")
    review_count = st.slider("每个 App 抓取评论数", min_value=50, max_value=300, value=150, step=50)
    submitted = st.form_submit_button("开始分析 →", type="primary", use_container_width=True)

# ── Pipeline ───────────────────────────────────────────────────────────────
if submitted and main_app.strip():
    apps_input = [main_app.strip()]
    if competitor_input.strip():
        apps_input += [c.strip() for c in competitor_input.split(",") if c.strip()]

    all_analyses = {}
    app_meta = {}

    with st.status("正在运行分析流程...", expanded=True) as status:
        for app_query in apps_input:
            st.write(f"🔎 搜索「{app_query}」...")
            results = search_app(app_query, country=country)
            if not results:
                st.warning(f"未在 {country.upper()} 区找到「{app_query}」，已跳过")
                continue

            info = results[0]
            app_meta[info["name"]] = info
            st.write(f"📥 抓取「{info['name']}」评论（最多 {review_count} 条）...")
            reviews = get_reviews(info["name"], info["id"], country=country, count=review_count)
            if not reviews:
                st.warning(f"「{info['name']}」暂无评论数据，已跳过")
                continue

            st.write(f"🤖 分析「{info['name']}」的 {len(reviews)} 条评论...")
            analysis = analyze_app_reviews(info["name"], reviews)
            all_analyses[info["name"]] = analysis

        if not all_analyses:
            status.update(label="未获取到有效数据", state="error")
            st.stop()

        main_app_name = list(all_analyses.keys())[0]
        st.write("📊 生成竞品洞察与战略建议...")
        insights = generate_competitive_insights(all_analyses, main_app=main_app_name)
        status.update(label="✅ 分析完成！", state="complete")

    st.session_state["all_analyses"] = all_analyses
    st.session_state["app_meta"] = app_meta
    st.session_state["insights"] = insights
    st.session_state["main_app_name"] = main_app_name

if "all_analyses" not in st.session_state:
    st.stop()

all_analyses = st.session_state["all_analyses"]
app_meta = st.session_state["app_meta"]
insights = st.session_state["insights"]
main_app_name = st.session_state["main_app_name"]

# ── Section 1: Per-app review breakdown ────────────────────────────────────
st.divider()
st.header("📋 Step 1 · 各 App 用户反馈分析")

sentiment_emoji = {"positive": "😊", "mixed": "😐", "negative": "😞"}

for app_name, analysis in all_analyses.items():
    meta = app_meta.get(app_name, {})
    emoji = sentiment_emoji.get(analysis.get("overall_sentiment", "mixed"), "😐")
    with st.expander(
        f"{emoji} **{app_name}**  —  {meta.get('rating', 'N/A')} ⭐  ({meta.get('rating_count', 0):,} 人评分)",
        expanded=True,
    ):
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🔴 主要痛点")
            for item in analysis.get("top_pain_points", []):
                freq_color = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(item.get("frequency"), "⚪")
                st.markdown(f"{freq_color} **{item['issue']}**")
                st.caption(f'「{item.get("example_quote", "")}」')
        with col2:
            st.subheader("🟢 用户好评")
            for item in analysis.get("top_positives", []):
                freq_color = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(item.get("frequency"), "⚪")
                st.markdown(f"{freq_color} **{item['strength']}**")
                st.caption(f'「{item.get("example_quote", "")}」')

        if analysis.get("key_feature_requests"):
            st.subheader("💡 用户功能需求")
            for req in analysis["key_feature_requests"]:
                st.markdown(f"- {req}")

        st.info(f"**总结：** {analysis.get('summary', '')}")

# ── Section 2: Competitive insights ────────────────────────────────────────
st.divider()
st.header(f"🏆 Step 2 · 竞品洞察（以「{main_app_name}」为视角）")

col1, col2, col3 = st.columns(3)
with col1:
    st.subheader("🔴 必须跟进的差距")
    st.caption("竞品已解决，主产品尚未解决")
    for item in insights.get("must_close_gaps", []):
        badge = "🔴" if item.get("urgency") == "high" else "🟡"
        st.markdown(f"{badge} **{item['gap']}**")
        st.caption(f"参考：{item.get('competitor', '')}")
        st.write("")

with col2:
    st.subheader("🟡 先发制人的机会窗口")
    st.caption("双方都未解决，谁先做谁领先")
    for item in insights.get("opportunity_windows", []):
        st.markdown(f"**{item['opportunity']}**")
        st.caption(item.get("rationale", ""))
        st.write("")

with col3:
    st.subheader("🟢 已有优势，继续放大")
    st.caption("主产品领先竞品的地方")
    for item in insights.get("core_advantages", []):
        st.markdown(f"**{item['advantage']}**")
        st.caption(item.get("how_to_amplify", ""))
        st.write("")

st.subheader("📌 优先行动矩阵")
priority = insights.get("priority_matrix", [])
if priority:
    level_map = {"high": "高", "medium": "中", "low": "低"}
    st.table([{
        "行动项": i.get("action", ""),
        "影响力": level_map.get(i.get("impact", ""), i.get("impact", "")),
        "所需投入": level_map.get(i.get("effort", ""), i.get("effort", "")),
    } for i in priority])

st.subheader("📍 定位建议")
st.info(insights.get("positioning_recommendation", ""))

st.subheader("📝 战略总结")
st.success(insights.get("summary", ""))

# ── Section 3: PRD Generator ───────────────────────────────────────────────
st.divider()
st.header("📄 Step 3 · 需求草稿生成")
st.caption("选择一个机会点，自动生成带论证链的 PRD 草稿")

gap_options = [f"[必须跟进] {i['gap']}" for i in insights.get("must_close_gaps", [])]
window_options = [f"[机会窗口] {i['opportunity']}" for i in insights.get("opportunity_windows", [])]
selected = st.selectbox("选择要展开的机会点", gap_options + window_options)

if st.button("生成需求草稿 →", type="primary"):
    st.divider()
    placeholder = st.empty()
    full_text = ""
    pending = ""
    for chunk in stream_prd_draft(selected, all_analyses, insights):
        full_text += chunk
        pending += chunk
        if len(pending) >= 20:
            placeholder.markdown(full_text + "▌")
            pending = ""
    placeholder.markdown(full_text)
