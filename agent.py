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

# Tavily 可选，有 key 才启用
try:
    from tavily import TavilyClient
    _tavily_key = os.getenv("TAVILY_API_KEY", "")
    tavily = TavilyClient(api_key=_tavily_key) if _tavily_key else None
except ImportError:
    tavily = None

# ── 工具定义 ────────────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "get_app_reviews",
        "description": (
            "在 App Store 中搜索指定 App 并抓取真实用户评论。"
            "用于获取某个 App 的用户反馈数据，作为分析的原始素材。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "App 名称，如「小红书」「抖音」"
                },
                "country": {
                    "type": "string",
                    "description": "App Store 国家/地区代码，默认 cn",
                    "default": "cn"
                },
                "count": {
                    "type": "integer",
                    "description": "抓取评论数量，建议 100-200",
                    "default": 150
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "搜索互联网获取 App 或公司的最新动态、功能更新、行业新闻。"
            "用于补充 App Store 评论未能覆盖的最新产品信息。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，如「小红书 2025 新功能」"
                }
            },
            "required": ["query"]
        }
    }
]

# ── 系统提示词 ──────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一位资深产品经理，负责为主产品做竞品洞察分析。

你有两个工具：
1. get_app_reviews：获取某 App 的真实用户评论（必须调用）
2. web_search：搜索产品最新动态和新闻（可选，用于补充评论数据）

工作步骤：
1. 对主产品和每个竞品各调用一次 get_app_reviews 获取评论
2. 如有必要，用 web_search 补充近期产品动态
3. 收集完所有数据后，输出完整分析报告

最终输出 JSON，直接以 { 开头，不要 markdown 代码块：
{
  "app_analyses": {
    "App名称": {
      "top_pain_points": [{"issue": "...", "frequency": "high/medium/low", "example_quote": "原文引用"}],
      "top_positives": [{"strength": "...", "frequency": "high/medium/low", "example_quote": "原文引用"}],
      "overall_sentiment": "positive/mixed/negative",
      "key_feature_requests": ["需求1", "需求2"],
      "summary": "2-3句总结"
    }
  },
  "competitive_insights": {
    "must_close_gaps": [{"gap": "...", "competitor": "...", "urgency": "high/medium"}],
    "opportunity_windows": [{"opportunity": "...", "rationale": "..."}],
    "core_advantages": [{"advantage": "...", "how_to_amplify": "..."}],
    "priority_matrix": [{"action": "...", "impact": "high/medium/low", "effort": "high/medium/low"}],
    "positioning_recommendation": "差异化定位建议（2-3句）",
    "summary": "战略总结（3-4句）"
  }
}

要求：
- example_quote 使用「」不用英文引号
- 分析以主产品视角为中心
- pain_points 和 positives 各 3-5 条"""

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


# ── 工具执行 ────────────────────────────────────────────────────────────────
def run_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "get_app_reviews":
        app_name = tool_input["app_name"]
        country = tool_input.get("country", "cn")
        count = tool_input.get("count", 150)

        results = search_app(app_name, country=country)
        if not results:
            return f"未找到 App：{app_name}"

        info = results[0]
        reviews = get_reviews(info["name"], info["id"], country=country, count=count)
        if not reviews:
            return f"「{info['name']}」暂无评论数据"

        return json.dumps({
            "app_name": info["name"],
            "rating": info["rating"],
            "rating_count": info["rating_count"],
            "review_count": len(reviews),
            "reviews": reviews
        }, ensure_ascii=False)

    elif tool_name == "web_search":
        if not tavily:
            return "web_search 不可用（未配置 TAVILY_API_KEY）"
        query = tool_input["query"]
        results = tavily.search(query=query, search_depth="basic", max_results=5)
        formatted = []
        for r in results.get("results", []):
            formatted.append(f"**{r['title']}**\n{r['url']}\n{r['content'][:400]}")
        return "\n---\n".join(formatted)

    return "未知工具"


# ── JSON 解析（带修复）──────────────────────────────────────────────────────
def _parse_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "修复下面损坏的 JSON，只输出合法 JSON：\n\n" + raw[:6000]}]
    )
    return json.loads(resp.content[0].text.strip())


# ── Agent 主循环 ────────────────────────────────────────────────────────────
def run_agent(main_app: str, competitors: list[str], country: str = "cn",
              count: int = 150, on_status=None) -> dict:
    """
    真正的 Agent：Claude 自主决定调用哪些工具、何时停止。
    返回结构化分析结果 dict。
    """
    task = f"请分析主产品「{main_app}」"
    if competitors:
        task += f"，竞品为：{'、'.join(competitors)}"
    task += f"。App Store 地区：{country}，每个 App 抓取约 {count} 条评论。请先获取各产品评论，再输出完整分析报告。"

    messages = [{"role": "user", "content": task}]

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages
        )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    # 实时状态回调
                    if on_status:
                        if block.name == "get_app_reviews":
                            on_status("tool", f"📥 抓取「{block.input.get('app_name')}」的用户评论...")
                        elif block.name == "web_search":
                            on_status("tool", f"🔎 搜索：{block.input.get('query')}")

                    result = run_tool(block.name, block.input)

                    if on_status:
                        on_status("done", f"✅ 完成：{block.name}")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text") and block.text.strip():
                    return _parse_json(block.text)
            break

    return {}


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
