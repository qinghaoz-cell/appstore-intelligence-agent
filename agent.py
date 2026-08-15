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
MODEL_NAME = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
client = Anthropic()

# Tavily 可选
try:
    from tavily import TavilyClient
    _tavily_key = os.getenv("TAVILY_API_KEY", "")
    tavily = TavilyClient(api_key=_tavily_key) if _tavily_key else None
except ImportError:
    tavily = None

MAX_RESEARCH_ACTIONS = 5

WEB_SEARCH_TOOL = {
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

REVIEW_EVIDENCE_TOOL = {
    "name": "inspect_review_evidence",
    "description": "当摘要证据不足时，查看指定 App 的原始用户评论节选，用于核实某个痛点、优势或需求。",
    "input_schema": {
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "要查看的 App 名称"},
            "focus": {"type": "string", "description": "希望核实的主题或假设"}
        },
        "required": ["app_name", "focus"]
    }
}

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


def _analyze_app(app_name: str, reviews: list[str]) -> dict:
    cleaned = [_clean(r) for r in reviews]
    reviews_text = "\n".join([f"- {r}" for r in cleaned])

    prompt = f"""分析「{app_name}」的用户反馈，直接输出 JSON，以 {{ 开头：

{{
  "top_pain_points": [{{"issue": "...", "frequency": "high/medium/low", "example_quote": "「原文或概括」"}}],
  "top_positives": [{{"strength": "...", "frequency": "high/medium/low", "example_quote": "「原文或概括」"}}],
  "overall_sentiment": "positive/mixed/negative",
  "key_feature_requests": ["需求1", "需求2", "需求3"],
  "summary": "2-3句总结"
}}

要求：pain_points 和 positives 各 3 条，若数据不足可适当减少，example_quote 用「」。
如果数据极少，仍需输出合法 JSON，用已有信息尽力填充。

用户反馈数据：
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
    result.setdefault("summary", "数据较少，分析结果仅供参考")
    return result


# ── 竞品洞察生成（带研究工具的 Agent 循环）─────────────────────────────────
def _review_evidence(review_evidence: dict, app_name: str, focus: str) -> str:
    """返回有限的原始评论节选，防止一次工具调用塞入过多上下文。"""
    reviews = review_evidence.get(app_name, [])
    if not reviews:
        return f"未找到「{app_name}」的原始评论；请基于已有分析或改查其他产品。"
    excerpts = "\n".join(f"- {review}" for review in reviews[:12])
    return f"「{app_name}」围绕「{focus}」的可用评论节选：\n{excerpts}"


def _generate_insights(app_analyses: dict, main_app: str, review_evidence: dict,
                       on_status=None) -> dict:
    competitors = [n for n in app_analyses if n != main_app]
    analyses_text = json.dumps(app_analyses, ensure_ascii=False, indent=2)

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
                        on_status("tool", f"🔎 搜索：{block.input.get('query')}")
                    query = block.input.get("query", "")
                    try:
                        results = tavily.search(query=query, search_depth="basic", max_results=3)
                        content = "\n---\n".join(
                            f"{r['title']}\n{r['content'][:300]}"
                            for r in results.get("results", [])
                        )
                    except Exception:
                        content = "搜索暂时不可用，请基于评论数据进行分析"
                    if on_status:
                        on_status("done", f"✅ 搜索完成")
                    research_trace.append({
                        "action": "搜索最新信息",
                        "target": query,
                        "reason": "补充评论未覆盖的近期信息",
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
                        on_status("tool", f"🧾 核查「{app_name}」评论证据：{focus}")
                    content = _review_evidence(review_evidence, app_name, focus)
                    research_trace.append({
                        "action": "核查原始评论",
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
                            "coverage": "基于已获取的评论分析",
                            "remaining_uncertainty": "未提供研究自评",
                        })
                        return result
            break
        else:
            raise RuntimeError(f"竞品洞察未完成（停止原因：{resp.stop_reason}）。请稍后重试。")

    raise RuntimeError("竞品洞察没有返回有效 JSON，请稍后重试。")


# ── Agent 主循环 ────────────────────────────────────────────────────────────
def run_agent(main_app: str, competitors: list[str], country: str = "cn",
              count: int = 100, on_status=None, on_app_analysis=None) -> dict:
    """
    分阶段运行：逐个抓取评论并分析，每完成一个 App 立即回调展示。
    最后生成竞品洞察。
    """
    all_apps = [main_app] + competitors
    app_analyses = {}
    review_evidence = {}

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
        review_evidence[app_name] = trimmed

        if on_status:
            on_status("done", f"✅ 「{app_name}」分析完成")

        # 立即回调，让前端展示这个 App 的卡片
        if on_app_analysis:
            on_app_analysis(app_name, analysis)

    if not app_analyses:
        return {}

    if on_status:
        on_status("tool", "📊 生成竞品洞察与战略建议...")

    insights = _generate_insights(
        app_analyses, main_app, review_evidence, on_status=on_status
    )

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
        model=MODEL_NAME,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            yield text
