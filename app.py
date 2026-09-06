import streamlit as st

from agent import run_agent, stream_prd_draft


COPY = {
    "zh": {
        "title": "🔍 App Store 竞品洞察 Agent",
        "caption": "输入 App 名称，Agent 自动抓取用户评论并分析用户痛点、竞品差距，一键生成需求草稿。",
        "main": "主产品名称", "main_example": "例如：小红书",
        "country": "App Store 地区", "competitors": "竞品名称（可选，英文逗号分隔）",
        "competitor_example": "例如：抖音, 微博", "start": "开始分析 →",
        "running": "🤖 Agent 正在运行...", "failed": "分析失败", "complete": "✅ 分析完成！",
        "unavailable": "服务暂时不可用，请稍后重试；如持续出现，请查看 Streamlit Cloud 日志。",
        "no_data": "未获取到有效数据。", "step1": "📋 Step 1 · 各 App 用户反馈分析",
        "pain": "🔴 主要痛点", "positive": "🟢 用户好评", "requests": "💡 用户功能需求", "summary": "总结",
        "step2": "🏆 Step 2 · 竞品洞察（以「{app}」为视角）",
        "gaps": "🔴 必须跟进的差距", "gaps_hint": "竞品已解决，主产品尚未解决",
        "opportunities": "🟡 先发制人的机会窗口", "opportunities_hint": "双方都未解决，谁先做谁领先",
        "advantages": "🟢 已有优势，继续放大", "advantages_hint": "主产品领先竞品的地方",
        "competitor": "参考", "evidence": "证据", "research": "🔎 Agent 研究过程",
        "confidence": "结论置信度", "coverage": "覆盖范围", "uncertainty": "待验证", "reason": "原因",
        "trace": "查看 Agent 的 {count} 次补充研究", "priority": "📌 优先行动矩阵",
        "action": "行动项", "impact": "影响力", "effort": "所需投入",
        "positioning": "📍 定位建议", "strategy": "📝 战略总结",
        "step3": "📄 Step 3 · 需求草稿生成", "step3_hint": "选择一个机会点，自动生成带论证链的 PRD 草稿。",
        "select": "选择要展开的机会点", "generate": "生成需求草稿 →",
        "gap_prefix": "[必须跟进]", "opportunity_prefix": "[机会窗口]",
        "high": "高", "medium": "中", "low": "低",
    },
    "en": {
        "title": "🔍 App Store Competitive Intelligence Agent",
        "caption": "Turn App Store reviews into evidence-backed competitive insights and product-requirement drafts.",
        "main": "Your product", "main_example": "e.g., TikTok",
        "country": "App Store storefront", "competitors": "Competitors (optional, comma-separated)",
        "competitor_example": "e.g., Instagram, YouTube", "start": "Start research →",
        "running": "🤖 Research Agent is running...", "failed": "Analysis failed", "complete": "✅ Research complete",
        "unavailable": "The service is temporarily unavailable. Please try again later; if the issue persists, check the Streamlit Cloud logs.",
        "no_data": "No usable data was returned.", "step1": "📋 Step 1 · User feedback by app",
        "pain": "🔴 Key pain points", "positive": "🟢 What users value", "requests": "💡 Feature requests", "summary": "Summary",
        "step2": "🏆 Step 2 · Competitive insights for {app}",
        "gaps": "🔴 Gaps to close", "gaps_hint": "Competitor strengths your product may need to match",
        "opportunities": "🟡 Opportunity windows", "opportunities_hint": "Unmet needs where either product could lead",
        "advantages": "🟢 Existing advantages to amplify", "advantages_hint": "Where your product is already ahead",
        "competitor": "Competitor", "evidence": "Evidence", "research": "🔎 Agent research process",
        "confidence": "Confidence", "coverage": "Coverage", "uncertainty": "Needs validation", "reason": "Reason",
        "trace": "View the Agent's {count} supporting research actions", "priority": "📌 Priority action matrix",
        "action": "Action", "impact": "Impact", "effort": "Effort",
        "positioning": "📍 Positioning recommendation", "strategy": "📝 Strategic summary",
        "step3": "📄 Step 3 · Generate a product-requirement draft", "step3_hint": "Choose an opportunity to turn evidence into a structured PRD draft.",
        "select": "Choose an opportunity to develop", "generate": "Generate PRD draft →",
        "gap_prefix": "[Gap to close]", "opportunity_prefix": "[Opportunity window]",
        "high": "High", "medium": "Medium", "low": "Low",
    },
}

st.set_page_config(page_title="App Store Competitive Intelligence Agent", page_icon="🔍", layout="wide")

title_col, language_col = st.columns([5, 1])
with language_col:
    language_label = st.selectbox("语言 / Language", ["中文", "English"], index=0, label_visibility="visible")
language = "zh" if language_label == "中文" else "en"
t = COPY[language]
with title_col:
    st.title(t["title"])
st.caption(t["caption"])

sentiment_emoji = {"positive": "😊", "mixed": "😐", "negative": "😞"}


def show_app_card(app_name, analysis):
    emoji = sentiment_emoji.get(analysis.get("overall_sentiment", "mixed"), "😐")
    with st.expander(f"{emoji} **{app_name}**", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            st.subheader(t["pain"])
            for item in analysis.get("top_pain_points", []):
                marker = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(item.get("frequency"), "⚪")
                st.markdown(f"{marker} **{item.get('issue', '')}**")
                st.caption(f'“{item.get("example_quote", "")}”')
        with col2:
            st.subheader(t["positive"])
            for item in analysis.get("top_positives", []):
                marker = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(item.get("frequency"), "⚪")
                st.markdown(f"{marker} **{item.get('strength', '')}**")
                st.caption(f'“{item.get("example_quote", "")}”')
        if analysis.get("key_feature_requests"):
            st.subheader(t["requests"])
            for request in analysis["key_feature_requests"]:
                st.markdown(f"- {request}")
        st.info(f"**{t['summary']}:** {analysis.get('summary', '')}")


with st.form("input_form"):
    col1, col2 = st.columns([3, 1])
    with col1:
        main_app = st.text_input(t["main"], placeholder=t["main_example"])
    with col2:
        country = st.selectbox(t["country"], ["cn", "us"] if language == "zh" else ["us", "cn"])
    competitor_input = st.text_input(t["competitors"], placeholder=t["competitor_example"])
    submitted = st.form_submit_button(t["start"], type="primary", use_container_width=True)

if submitted and main_app.strip():
    competitors = [item.strip() for item in competitor_input.split(",") if item.strip()]
    with st.status(t["running"], expanded=True) as status:
        def on_status(event_type, message):
            st.write(message)

        st.divider()
        st.header(t["step1"])
        step1_container = st.container()

        def on_app_analysis(app_name, analysis):
            with step1_container:
                show_app_card(app_name, analysis)

        try:
            result = run_agent(
                main_app=main_app.strip(), competitors=competitors, country=country,
                count=50, on_status=on_status, on_app_analysis=on_app_analysis, language=language,
            )
        except RuntimeError as exc:
            status.update(label=t["failed"], state="error")
            st.error(str(exc))
            st.stop()
        except Exception as exc:
            status.update(label=t["failed"], state="error")
            detail = str(exc).strip().replace("\n", " ")[:500]
            suffix = f"{exc.__class__.__name__}{f': {detail}' if detail else ''}"
            st.error(f"{t['unavailable']} ({suffix})")
            st.stop()
        if not result:
            status.update(label=t["no_data"], state="error")
            st.stop()
        status.update(label=t["complete"], state="complete")

    st.session_state["app_analyses"] = result.get("app_analyses", {})
    st.session_state["insights"] = result.get("competitive_insights", {})
    st.session_state["main_app_name"] = main_app.strip()
    st.session_state["result_language"] = language

if "app_analyses" not in st.session_state:
    st.stop()

app_analyses = st.session_state["app_analyses"]
insights = st.session_state["insights"]
main_app_name = st.session_state["main_app_name"]
result_language = st.session_state.get("result_language", language)

if result_language != language:
    st.info(
        "切换语言后，请重新运行分析以生成对应语言的洞察与 PRD。"
        if language == "zh" else
        "After switching languages, run a new analysis to generate matching insights and PRD output."
    )
    st.stop()

st.divider()
st.header(t["step1"])
for app_name, analysis in app_analyses.items():
    show_app_card(app_name, analysis)

st.divider()
st.header(t["step2"].format(app=main_app_name))
cols = st.columns(3)
sections = [
    ("must_close_gaps", "gaps", "gaps_hint", "gap", ""),
    ("opportunity_windows", "opportunities", "opportunities_hint", "opportunity", "rationale"),
    ("core_advantages", "advantages", "advantages_hint", "advantage", "how_to_amplify"),
]
for col, (key, heading, hint, field, supporting) in zip(cols, sections):
    with col:
        st.subheader(t[heading])
        st.caption(t[hint])
        for item in insights.get(key, []):
            prefix = "🔴 " if key == "must_close_gaps" and item.get("urgency") == "high" else ""
            st.markdown(f"{prefix}**{item.get(field, '')}**")
            if key == "must_close_gaps":
                st.caption(f"{t['competitor']}: {item.get('competitor', '')}")
            elif supporting:
                st.caption(item.get(supporting, ""))
            if item.get("evidence"):
                st.caption(f"{t['evidence']}: {item['evidence']}")
            st.write("")

assessment, trace = insights.get("research_assessment", {}), insights.get("research_trace", [])
if assessment or trace:
    st.subheader(t["research"])
    if assessment:
        confidence = assessment.get("confidence", "")
        st.caption(f"{t['confidence']}: {t.get(confidence, confidence or '')} | {t['coverage']}: {assessment.get('coverage', '')}")
        if assessment.get("remaining_uncertainty"):
            st.caption(f"{t['uncertainty']}: {assessment['remaining_uncertainty']}")
    if trace:
        with st.expander(t["trace"].format(count=len(trace)), expanded=False):
            for index, item in enumerate(trace, start=1):
                st.markdown(f"{index}. **{item.get('action', '')}** · {item.get('target', '')}")
                st.caption(f"{t['reason']}: {item.get('reason', '')}")

st.subheader(t["priority"])
priority = insights.get("priority_matrix", [])
if priority:
    st.table([{t["action"]: item.get("action", ""), t["impact"]: t.get(item.get("impact", ""), item.get("impact", "")), t["effort"]: t.get(item.get("effort", ""), item.get("effort", ""))} for item in priority])
st.subheader(t["positioning"])
st.info(insights.get("positioning_recommendation", ""))
st.subheader(t["strategy"])
st.success(insights.get("summary", ""))

st.divider()
st.header(t["step3"])
st.caption(t["step3_hint"])
options = ([f"{t['gap_prefix']} {item['gap']}" for item in insights.get("must_close_gaps", [])] + [f"{t['opportunity_prefix']} {item['opportunity']}" for item in insights.get("opportunity_windows", [])])
selected = st.selectbox(t["select"], options)
if st.button(t["generate"], type="primary"):
    placeholder, full_text, pending = st.empty(), "", ""
    for chunk in stream_prd_draft(selected, app_analyses, insights, language=result_language):
        full_text += chunk
        pending += chunk
        if len(pending) >= 20:
            placeholder.markdown(full_text + "▌")
            pending = ""
    placeholder.markdown(full_text)
