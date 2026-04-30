import json
import os
from pathlib import Path
from anthropic import Anthropic

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

client = Anthropic()

REVIEW_ANALYSIS_PROMPT = """你是一位资深产品分析师。请分析以下 App Store 评论，提炼用户真实反馈。

App 名称：{app_name}
评论数量：{count} 条

评论内容：
{reviews}

只输出 JSON，不要任何解释文字，不要 markdown 代码块，直接以 { 开头：
{
  "top_pain_points": [
    {"issue": "问题描述", "frequency": "high/medium/low", "example_quote": "原文引用"}
  ],
  "top_positives": [
    {"strength": "优点描述", "frequency": "high/medium/low", "example_quote": "原文引用"}
  ],
  "overall_sentiment": "positive/mixed/negative",
  "key_feature_requests": ["功能需求1", "功能需求2"],
  "summary": "2-3句总结"
}

要求：
- pain_points 和 positives 各提炼 3-5 条
- example_quote 直接来自评论原文，不得包含英文双引号，用「」替代
- frequency 基于提及频率判断"""

COMPETITIVE_INSIGHT_PROMPT = """你是 {main_app} 的产品经理，正在基于用户反馈数据做竞品分析。

主产品：{main_app}
竞品：{competitors}

各产品用户反馈分析数据：
{analyses}

只输出 JSON，不要任何解释文字，不要 markdown 代码块，直接以 { 开头：
{
  "must_close_gaps": [
    {"gap": "竞品已解决但主产品尚未解决的问题", "competitor": "哪个竞品做得更好", "urgency": "high/medium"}
  ],
  "opportunity_windows": [
    {"opportunity": "双方都没解决的共同痛点", "rationale": "先解决的先发优势"}
  ],
  "core_advantages": [
    {"advantage": "主产品领先竞品的地方", "how_to_amplify": "如何放大此优势"}
  ],
  "priority_matrix": [
    {"action": "优先行动项", "impact": "high/medium/low", "effort": "high/medium/low"}
  ],
  "positioning_recommendation": "差异化定位建议（2-3句）",
  "summary": "以主产品视角的战略总结（3-4句）"
}

要求：
- must_close_gaps：3-5条，竞品明显更好的地方
- opportunity_windows：2-4条，双方共同痛点
- core_advantages：2-4条，主产品真实领先的地方
- priority_matrix：3-5条，按影响力和投入排序"""

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
    # Last resort: ask Claude to repair
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": (
            "修复下面损坏的 JSON，只输出合法 JSON，直接以 { 开头：\n\n" + raw[:6000]
        )}]
    )
    repaired = resp.content[0].text.strip()
    if repaired.startswith("```"):
        repaired = repaired.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(repaired)


def _call(prompt: str) -> str:
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text


def analyze_app_reviews(app_name: str, reviews: list[str]) -> dict:
    sample = reviews[:200]
    reviews_text = "\n".join([f"- {r}" for r in sample])
    prompt = (
        REVIEW_ANALYSIS_PROMPT
        .replace("{app_name}", app_name)
        .replace("{count}", str(len(reviews)))
        .replace("{reviews}", reviews_text)
    )
    return _parse_json(_call(prompt))


def generate_competitive_insights(app_analyses: dict, main_app: str) -> dict:
    competitors = [n for n in app_analyses if n != main_app]
    analyses_text = json.dumps(app_analyses, ensure_ascii=False, indent=2)
    prompt = (
        COMPETITIVE_INSIGHT_PROMPT
        .replace("{main_app}", main_app)
        .replace("{competitors}", "、".join(competitors) if competitors else "无")
        .replace("{analyses}", analyses_text)
    )
    return _parse_json(_call(prompt))


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
