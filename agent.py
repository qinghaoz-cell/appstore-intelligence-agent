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

client = Anthropic()

# Tavily 可选
try:
    from tavily import TavilyClient
    _tavily_key = os.getenv("TAVILY_API_KEY", "")
    tavily = TavilyClient(api_key=_tavily_key) if _tavily_key else None
except ImportError:
    tavily = None

INSIGHT_TOOLS = [
    {
        "name": "web_search",
        "description": "搜索产品最新动态、功能更新、行业新闻，补充评论数据未覆盖的近期信息。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"}
            },
            "required": ["query"]
        }
    }
]

# ── PRD 生成提示词 ──────────────────────────────────────────────────────────
PRD_GENERATION_PROMPT = """你是一位资深产品经理，请基于以下用户研究和竞品分析，为选定的机会点生成结构化需求草稿。

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


# ── JSON 解析（带修复）──────────────────────────────────────────────────────
def _extract_json(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1:
        return s[start:end + 1]
    return s


def _parse_json(text: str) -> dict:
    candidate = _extract_json(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": (
            "下面是损坏的 JSON，请直接输出修复后的完整合法 JSON，"
            "不要任何解释文字，不要 markdown 代码块，直接以 { 开头：\n\n"
            + candidate[:8000]
        )}]
    )
    repaired = _extract_json(resp.content[0].text)
    return json.loads(repaired)


# ── 单个 App 评论分析 ────────────────────────────────────────────────────────
def _analyze_app(app_name: str, reviews: list[str]) -> dict:
    reviews_text = "\n".join([f"- {r}" for r in reviews])
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": f"""分析「{app_name}」App Store 评论，直接输出 JSON，以 {{ 开头：

{{
  "top_pain_points": [{{"issue": "...", "frequency": "high/medium/low", "example_quote": "「原文」"}}],
  "top_positives": [{{"strength": "...", "frequency": "high/medium/low", "example_quote": "「原文」"}}],
  "overall_sentiment": "positive/mixed/negative",
  "key_feature_requests": ["需求1", "需求2", "需求3"],
  "summary": "2-3句总结"
}}

要求：pain_points 和 positives 各 3 条，example_quote 用「」不用英文引号。

评论数据：
{reviews_text}"""}]
    )
    return _parse_json(resp.content[0].text)


# ── 竞品洞察生成（带 web_search 的 Agent 循环）────────────────────────────
def _generate_insights(app_analyses: dict, main_app: str, on_status=None) -> dict:
    competitors = [n for n in app_analyses if n != main_app]
    analyses_text = json.dumps(app_analyses, ensure_ascii=False, indent=2)

    system = f"""你是「{main_app}」的产品经理，基于用户评论分析数据生成竞品洞察报告。
你可以使用 web_search 工具搜索产品最新动态（按需，最多 2 次）。
收集完信息后，直接输出 JSON，以 {{ 开头，不要 markdown 代码块：
{{
  "must_close_gaps": [{{"gap": "...", "competitor": "...", "urgency": "high/medium"}}],
  "opportunity_windows": [{{"opportunity": "...", "rationale": "..."}}],
  "core_advantages": [{{"advantage": "...", "how_to_amplify": "..."}}],
  "priority_matrix": [{{"action": "...", "impact": "high/medium/low", "effort": "high/medium/low"}}],
  "positioning_recommendation": "差异化定位建议（2-3句）",
  "summary": "战略总结（3-4句）"
}}
各类各 3 条，以「{main_app}」视角为中心。"""

    task = f"竞品：{'、'.join(competitors) if competitors else '无'}\n\n各产品用户分析：\n{analyses_text}"
    messages = [{"role": "user", "content": task}]
    tools = INSIGHT_TOOLS if tavily else []

    while True:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=messages
        )

        if resp.stop_reason == "tool_use":
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use" and block.name == "web_search":
                    if on_status:
                        on_status("tool", f"🔎 搜索：{block.input.get('query')}")
                    query = block.input.get("query", "")
                    results = tavily.search(query=query, search_depth="basic", max_results=3)
                    content = "\n---\n".join(
                        f"{r['title']}\n{r['content'][:300]}"
                        for r in results.get("results", [])
                    )
                    if on_status:
                        on_status("done", f"✅ 搜索完成")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content
                    })
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({"role": "user", "content": tool_results})

        elif resp.stop_reason == "end_turn":
            for block in resp.content:
                if hasattr(block, "text") and block.text.strip():
                    return _parse_json(block.text)
            break

    return {}


# ── Agent 主循环 ────────────────────────────────────────────────────────────
def run_agent(main_app: str, competitors: list[str], country: str = "cn",
              count: int = 100, on_status=None, on_app_analysis=None) -> dict:
    """
    分阶段运行：逐个抓取评论并分析，每完成一个 App 立即回调展示。
    最后生成竞品洞察。
    """
    all_apps = [main_app] + competitors
    app_analyses = {}

    for app_query in all_apps:
        if on_status:
            on_status("tool", f"📥 搜索「{app_query}」...")

        results = search_app(app_query, country=country)
        if not results:
            if on_status:
                on_status("done", f"⚠️ 未找到「{app_query}」，已跳过")
            continue

        info = results[0]
        app_name = info["name"]

        if on_status:
            on_status("tool", f"📥 抓取「{app_name}」评论...")

        reviews = get_reviews(app_name, info["id"], country=country, count=count)
        if not reviews:
            if on_status:
                on_status("done", f"⚠️ 「{app_name}」暂无评论数据，已跳过")
            continue

        trimmed = [r[:200] for r in reviews[:50]]

        if on_status:
            on_status("tool", f"🤖 分析「{app_name}」用户评论...")

        analysis = _analyze_app(app_name, trimmed)
        app_analyses[app_name] = analysis

        if on_status:
            on_status("done", f"✅ 「{app_name}」分析完成")

        # 立即回调，让前端展示这个 App 的卡片
        if on_app_analysis:
            on_app_analysis(app_name, analysis)

    if not app_analyses:
        return {}

    if on_status:
        on_status("tool", "📊 生成竞品洞察与战略建议...")

    insights = _generate_insights(app_analyses, main_app, on_status=on_status)

    if on_status:
        on_status("done", "✅ 竞品洞察完成")

    return {
        "app_analyses": app_analyses,
        "competitive_insights": insights
    }


# ── PRD 流式生成 ────────────────────────────────────────────────────────────
def stream_prd_draft(opportunity: str, all_analyses: dict, insights: dict):
    analyses_text = json.dumps(all_analyses, ensure_ascii=False, indent=2)
    insights_text = json.dumps(insights, ensure_ascii=False, indent=2)
    prompt = (
        PRD_GENERATION_PROMPT
        .replace("{opportunity}", opportunity)
        .replace("{all_analyses}", analyses_text)
        .replace("{insights}", insights_text)
    )
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            yield text
