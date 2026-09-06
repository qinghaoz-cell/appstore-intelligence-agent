import json
import os
from pathlib import Path
from anthropic import Anthropic
from scraper import search_app, get_reviews

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

# 可在 Streamlit Secrets 或 .env 中通过 ANTHROPIC_MODEL 覆盖。
# 集中管理模型名，避免某个调用仍使用失效模型。
MODEL_NAME = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
client = Anthropic()

# Tavily 可选
try:
    from tavily import TavilyClient
    _tavily_key = os.getenv("TAVILY_API_KEY", "")
    tavily = TavilyClient(api_key=_tavily_key) if _tavily_key else None
except ImportError:
    tavily = None

MAX_RESEARCH_ACTIONS = 5
ANALYSIS_SAMPLE_SIZE = 50

WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": "Search public sources for recent product updates, feature changes, or market context that app reviews do not cover.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A focused search query"}
        },
        "required": ["query"]
    }
}

REVIEW_EVIDENCE_TOOL = {
    "name": "inspect_review_evidence",
    "description": "Inspect a limited excerpt of raw reviews for a selected app when the summary does not sufficiently support a conclusion.",
    "input_schema": {
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "The app to inspect"},
            "focus": {"type": "string", "description": "The claim, theme, or hypothesis to verify"}
        },
        "required": ["app_name", "focus"]
    }
}

# ── PRD 生成提示词 ──────────────────────────────────────────────────────────
PRD_GENERATION_PROMPT_ZH = """你是一位资深产品经理，请基于以下用户研究和竞品分析，为选定的机会点生成结构化需求草稿。

选定的机会点：{opportunity}

用户反馈分析：
{all_analyses}

竞品洞察：
{insights}

请用 Markdown 格式输出：

## 功能名称
（5字以内）

## 用户故事
As a [用户类型], I want to [具体行为], so that [获得价值]

## 问题陈述
（2-3句，说明严重性和普遍性）

## 用户原话佐证
（引用 2-3 条真实原话，注明来源 App）

## 竞品现状
（1-2句，说明竞品在此问题上的现状）

## 功能方案
（3-5句，描述做什么、怎么做）

## 验收标准
（3-5条可量化标准）

## 核心埋点指标
| 指标名称 | 定义 | 目标值 |
|---------|------|--------|

## 本期不做（Out of Scope）
（2-3条边界说明）"""

PRD_GENERATION_PROMPT_EN = """You are a senior product manager. Based on the user research and competitive analysis below, create a structured product-requirement draft for the selected opportunity.

Selected opportunity: {opportunity}

User-feedback analysis:
{all_analyses}

Competitive insights:
{insights}

Write the entire response in English and use this Markdown structure:

## Feature Name

## User Story
As a [user type], I want to [action], so that [value].

## Problem Statement
Explain the severity and prevalence in 2–3 sentences.

## Supporting User Evidence
Include 2–3 real quotes or faithful excerpts and name the source app.

## Competitive Context
Summarize how competitors address this problem in 1–2 sentences.

## Proposed Solution
Describe what to build and how it works in 3–5 sentences.

## Acceptance Criteria
List 3–5 measurable criteria.

## Key Metrics
| Metric | Definition | Target |
|--------|------------|--------|

## Out of Scope
List 2–3 explicit boundaries."""


# ── JSON 解析（带修复）──────────────────────────────────────────────────────
def _extract_json(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1:
        return s[start:end + 1]
    return s


def _format_api_error(exc: Exception) -> str:
    """提供可排查、且不泄露密钥的错误提示。"""
    status_code = getattr(exc, "status_code", None)
    detail = str(exc).strip()
    if status_code:
        return f"HTTP {status_code}{f'：{detail}' if detail else ''}"
    return detail or exc.__class__.__name__


def _parse_json(text: str) -> dict:
    candidate = _extract_json(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    try:
        resp = client.messages.create(
            model=MODEL_NAME,
            max_tokens=8192,
            messages=[{"role": "user", "content": (
                "下面是损坏的 JSON，请直接输出修复后的完整合法 JSON，"
                "不要任何解释文字，不要 markdown 代码块，直接以 { 开头：\n\n"
                + candidate[:8000]
            )}]
        )
        repaired = _extract_json(resp.content[0].text)
        return json.loads(repaired)
    except Exception as exc:
        raise RuntimeError("模型返回的分析结果无法解析为 JSON，请稍后重试。") from exc


# ── 单个 App 评论分析 ────────────────────────────────────────────────────────
def _clean(text: str) -> str:
    """清理特殊字符，避免破坏 JSON 输出"""
    return text.replace('"', '"').replace('"', '"').replace("\\", " ").replace("\n", " ").strip()


def _select_review_sample(reviews: list[dict], limit: int = ANALYSIS_SAMPLE_SIZE) -> list[dict]:
    """对近期评论去重后保留较大样本，供模型按主题聚类。"""
    records = [review if isinstance(review, dict) else {"text": str(review)} for review in reviews]
    selected, seen = [], set()
    for review in records:
        key = "".join(review.get("text", "").lower().split())
        if not key or key in seen or len(selected) >= limit:
            continue
        selected.append(review)
        seen.add(key)
    return selected


def _format_review_for_analysis(review: dict, language: str) -> str:
    return _clean(review.get("text", ""))


def _analyze_app(app_name: str, reviews: list[str], language: str = "en") -> dict:
    cleaned = [_clean(r) for r in reviews]
    reviews_text = "\n".join([f"- {r}" for r in cleaned])

    if language == "zh":
        prompt = f"""分析「{app_name}」的用户反馈，直接输出 JSON，以 {{ 开头：

{{
  "top_pain_points": [{{"issue": "...", "frequency": "high/medium", "support_count": 0, "example_quote": "「原文」"}}],
  "top_positives": [{{"strength": "...", "frequency": "high/medium/low", "example_quote": "「原文或概括」"}}],
  "overall_sentiment": "positive/mixed/negative",
  "key_feature_requests": ["需求1", "需求2", "需求3"],
  "summary": "2-3句总结"
}}

要求：先按语义将含义相近的评论聚成主题，再排序。pain_points 和 positives 各 3 条，若数据不足可适当减少，example_quote 必须是评论原文，用「」。
主要痛点只保留被至少 2 条不同评论支持的主题，并填写 support_count（该主题在当前样本中支持它的评论数）；频率 high 至少 5 条，medium 为 2-4 条。单条抱怨不要列为主要痛点。
如果数据极少，仍需输出合法 JSON，用已有信息尽力填充。

用户反馈数据：
{reviews_text}"""
    else:
        prompt = f"""Analyze user feedback for \"{app_name}\". Return valid JSON only, beginning with {{:

{{
  "top_pain_points": [{{"issue": "...", "frequency": "high/medium", "support_count": 0, "example_quote": "a real quote"}}],
  "top_positives": [{{"strength": "...", "frequency": "high/medium/low", "example_quote": "a real quote or faithful excerpt"}}],
  "overall_sentiment": "positive/mixed/negative",
  "key_feature_requests": ["request 1", "request 2", "request 3"],
  "summary": "A 2–3 sentence summary"
}}

Return all user-facing values in English. If reviews are in another language, translate their meaning faithfully. First cluster semantically similar reviews into themes, then rank themes. Provide up to three pain points and positives; when data is limited, return fewer items rather than inventing evidence. Quotes must be real review text. Only list a pain point if at least two distinct sampled reviews support its theme, and include that number as support_count; label high for 5+ supporting reviews and medium for 2–4. Do not list one-off complaints as key pain points.

User feedback:
{reviews_text}"""

    result = {}
    last_error = None
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=MODEL_NAME,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text if resp.content else ""
            result = _parse_json(raw)
            if result:
                break
        except Exception as exc:
            last_error = exc

    if not result:
        detail = _format_api_error(last_error) if last_error else "未返回有效内容"
        raise RuntimeError(f"评论分析调用失败：{detail}")

    result.setdefault("top_pain_points", [])
    result.setdefault("top_positives", [])
    result.setdefault("overall_sentiment", "mixed")
    result.setdefault("key_feature_requests", [])
    result.setdefault("summary", "数据较少，分析结果仅供参考" if language == "zh" else "Limited review data; interpret this analysis with caution.")
    return result


# ── 竞品洞察生成（带研究工具的 Agent 循环）─────────────────────────────────
def _review_evidence(review_evidence: dict, app_name: str, focus: str, language: str = "en") -> str:
    """返回有限的原始评论节选，防止一次工具调用塞入过多上下文。"""
    reviews = review_evidence.get(app_name, [])
    if not reviews:
        return (f"No raw reviews were found for \"{app_name}\". Use the existing analysis or inspect another app."
                if language == "en" else f"未找到「{app_name}」的原始评论；请基于已有分析或改查其他产品。")
    excerpts = "\n".join(f"- {review}" for review in reviews[:12])
    return (f"Available raw-review excerpts for \"{app_name}\" related to \"{focus}\":\n{excerpts}"
            if language == "en" else f"「{app_name}」围绕「{focus}」的可用评论节选：\n{excerpts}")


def _generate_insights(app_analyses: dict, main_app: str, review_evidence: dict,
                       on_status=None, language: str = "en") -> dict:
    competitors = [n for n in app_analyses if n != main_app]
    analyses_text = json.dumps(app_analyses, ensure_ascii=False, indent=2)

    if language == "zh":
        system = f"""你是「{main_app}」的竞品研究 Agent。你的目标不是罗列评论，而是形成有证据、可执行的产品决策。

先判断已有证据是否足够：
1. 摘要不足或某个结论需要核实时，调用 inspect_review_evidence 查看指定 App 的原始评论；
2. 评论无法覆盖近期功能或市场变化时，才调用 web_search 搜索；
3. 每条关键结论都要区分「评论证据」「公开信息」和「你的推断」。证据不足时，降低置信度或标为待验证，不要编造事实。
4. 工具调用总数上限为 {MAX_RESEARCH_ACTIONS} 次；信息足够后立即输出结论。

收集完成后，直接输出 JSON，以 {{ 开头，不要 markdown 代码块：
{{
  "must_close_gaps": [{{"gap": "...", "competitor": "...", "urgency": "high/medium", "evidence": "评论证据或公开信息"}}],
  "opportunity_windows": [{{"opportunity": "...", "rationale": "...", "evidence": "评论证据或公开信息"}}],
  "core_advantages": [{{"advantage": "...", "how_to_amplify": "...", "evidence": "评论证据或公开信息"}}],
  "priority_matrix": [{{"action": "...", "impact": "high/medium/low", "effort": "high/medium/low"}}],
  "positioning_recommendation": "差异化定位建议（2-3句）",
  "summary": "战略总结（3-4句）",
  "research_assessment": {{"confidence": "high/medium/low", "coverage": "已覆盖的产品和问题", "remaining_uncertainty": "仍需验证的点，没有则写无"}}
}}
各类各 3 条，以「{main_app}」视角为中心。"""
        task = f"竞品：{'、'.join(competitors) if competitors else '无'}\n\n各产品用户分析：\n{analyses_text}"
    else:
        system = f"""You are the competitive-research Agent for \"{main_app}\". Your goal is not to list reviews, but to produce evidence-backed, actionable product decisions.

First assess whether the available evidence is sufficient:
1. If a summary is insufficient or a conclusion needs verification, call inspect_review_evidence for the relevant app.
2. Call web_search only when reviews cannot answer a question about a recent feature change, product update, or market context.
3. Distinguish review evidence, public information, and your own inference in every key conclusion. When evidence is weak, lower confidence or flag it for validation; never invent facts.
4. You may make at most {MAX_RESEARCH_ACTIONS} total tool calls. Stop researching once the information is sufficient.

When finished, return valid JSON only, beginning with {{ and without Markdown:
{{
  "must_close_gaps": [{{"gap": "...", "competitor": "...", "urgency": "high/medium", "evidence": "review evidence or public information"}}],
  "opportunity_windows": [{{"opportunity": "...", "rationale": "...", "evidence": "review evidence or public information"}}],
  "core_advantages": [{{"advantage": "...", "how_to_amplify": "...", "evidence": "review evidence or public information"}}],
  "priority_matrix": [{{"action": "...", "impact": "high/medium/low", "effort": "high/medium/low"}}],
  "positioning_recommendation": "A 2–3 sentence differentiation recommendation",
  "summary": "A 3–4 sentence strategic summary",
  "research_assessment": {{"confidence": "high/medium/low", "coverage": "products and questions covered", "remaining_uncertainty": "questions that still need validation, or none"}}
}}
Return all user-facing values in English. Provide up to three items in each insight category and keep \"{main_app}\" as the decision-making point of view."""
        task = f"Competitors: {', '.join(competitors) if competitors else 'None'}\n\nUser-feedback analysis by app:\n{analyses_text}"

    messages = [{"role": "user", "content": task}]
    tools = [REVIEW_EVIDENCE_TOOL] + ([WEB_SEARCH_TOOL] if tavily else [])
    research_trace = []
    research_actions = 0

    while True:
        resp = client.messages.create(
            model=MODEL_NAME,
            max_tokens=4096,
            system=system,
            tools=tools if research_actions < MAX_RESEARCH_ACTIONS else [],
            messages=messages
        )

        if resp.stop_reason == "tool_use":
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use" and block.name == "web_search":
                    research_actions += 1
                    if on_status:
                        on_status("tool", f"🔎 Searching: {block.input.get('query')}" if language == "en" else f"🔎 搜索：{block.input.get('query')}")
                    query = block.input.get("query", "")
                    try:
                        results = tavily.search(query=query, search_depth="basic", max_results=3)
                        content = "\n---\n".join(
                            f"{r['title']}\n{r['content'][:300]}"
                            for r in results.get("results", [])
                        )
                    except Exception:
                        content = "Search is temporarily unavailable; continue with review evidence only." if language == "en" else "搜索暂时不可用，请基于评论数据进行分析"
                    if on_status:
                        on_status("done", "✅ Search complete" if language == "en" else "✅ 搜索完成")
                    research_trace.append({
                        "action": "Search recent public information" if language == "en" else "搜索最新信息",
                        "target": query,
                        "reason": "Supplement recent information not covered by reviews" if language == "en" else "补充评论未覆盖的近期信息",
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content
                    })
                elif block.type == "tool_use" and block.name == "inspect_review_evidence":
                    research_actions += 1
                    app_name = block.input.get("app_name", "")
                    focus = block.input.get("focus", "")
                    if on_status:
                        on_status("tool", f"🧾 Checking review evidence for {app_name}: {focus}" if language == "en" else f"🧾 核查「{app_name}」评论证据：{focus}")
                    content = _review_evidence(review_evidence, app_name, focus, language)
                    research_trace.append({
                        "action": "Inspect raw reviews" if language == "en" else "核查原始评论",
                        "target": app_name,
                        "reason": focus,
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                    })
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({"role": "user", "content": tool_results})

        elif resp.stop_reason == "end_turn":
            for block in resp.content:
                if hasattr(block, "text") and block.text.strip():
                    result = _parse_json(block.text)
                    if result:
                        result["research_trace"] = research_trace
                        result.setdefault("research_assessment", {
                            "confidence": "medium",
                            "coverage": "Based on the retrieved review analysis" if language == "en" else "基于已获取的评论分析",
                            "remaining_uncertainty": "No research self-assessment was provided" if language == "en" else "未提供研究自评",
                        })
                        return result
            break
        else:
            raise RuntimeError(f"竞品洞察未完成（停止原因：{resp.stop_reason}）。请稍后重试。")

    raise RuntimeError("竞品洞察没有返回有效 JSON，请稍后重试。")


# ── Agent 主循环 ────────────────────────────────────────────────────────────
def run_agent(main_app: str, competitors: list[str], country: str = "cn",
              count: int = 50, on_status=None, on_app_analysis=None, language: str = "zh") -> dict:
    """
    分阶段运行：逐个抓取评论并分析，每完成一个 App 立即回调展示。
    最后生成竞品洞察。
    """
    all_apps = [main_app] + competitors
    app_analyses = {}
    review_evidence = {}

    for app_query in all_apps:
        if on_status:
            on_status("tool", f"📥 Finding {app_query}..." if language == "en" else f"📥 搜索「{app_query}」...")

        results = search_app(app_query, country=country)
        if not results:
            if on_status:
                on_status("done", f"⚠️ {app_query} was not found; skipped" if language == "en" else f"⚠️ 未找到「{app_query}」，已跳过")
            continue

        info = results[0]
        app_name = info["name"]

        if on_status:
            on_status("tool", f"📥 Retrieving reviews for {app_name}..." if language == "en" else f"📥 抓取「{app_name}」评论...")

        reviews = get_reviews(app_name, info["id"], country=country, count=count, language=language)
        if not reviews:
            if on_status:
                on_status("done", f"⚠️ No review data for {app_name}; skipped" if language == "en" else f"⚠️ 「{app_name}」暂无评论数据，已跳过")
            continue

        sampled_reviews = _select_review_sample(reviews)
        trimmed = [_format_review_for_analysis(r, language)[:260] for r in sampled_reviews]

        if on_status:
            on_status("tool", f"🤖 Analyzing reviews for {app_name}..." if language == "en" else f"🤖 分析「{app_name}」用户评论...")

        analysis = _analyze_app(app_name, trimmed, language)
        analysis["review_sample"] = {
            "total": len(sampled_reviews),
            "recent": sum(r.get("sample_source") in {"recent", "both"} for r in sampled_reviews),
        }
        app_analyses[app_name] = analysis
        review_evidence[app_name] = [r.get("text", "")[:260] for r in sampled_reviews]

        if on_status:
            on_status("done", f"✅ {app_name} analysis complete" if language == "en" else f"✅ 「{app_name}」分析完成")

        # 立即回调，让前端展示这个 App 的卡片
        if on_app_analysis:
            on_app_analysis(app_name, analysis)

    if not app_analyses:
        return {}

    if on_status:
        on_status("tool", "📊 Generating competitive insights and recommendations..." if language == "en" else "📊 生成竞品洞察与战略建议...")

    insights = _generate_insights(
        app_analyses, main_app, review_evidence, on_status=on_status, language=language
    )

    if on_status:
        on_status("done", "✅ Competitive insights complete" if language == "en" else "✅ 竞品洞察完成")

    return {
        "app_analyses": app_analyses,
        "competitive_insights": insights
    }


# ── PRD 流式生成 ────────────────────────────────────────────────────────────
def stream_prd_draft(opportunity: str, all_analyses: dict, insights: dict, language: str = "en"):
    analyses_text = json.dumps(all_analyses, ensure_ascii=False, indent=2)
    insights_text = json.dumps(insights, ensure_ascii=False, indent=2)
    prompt = (
        (PRD_GENERATION_PROMPT_EN if language == "en" else PRD_GENERATION_PROMPT_ZH)
        .replace("{opportunity}", opportunity)
        .replace("{all_analyses}", analyses_text)
        .replace("{insights}", insights_text)
    )
    with client.messages.stream(
        model=MODEL_NAME,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            yield text
