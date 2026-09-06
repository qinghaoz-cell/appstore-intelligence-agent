import os
import time
import requests


def search_app(query: str, country: str = "cn") -> list[dict]:
    """Search iTunes API for an app by name, return top matches."""
    url = "https://itunes.apple.com/search"
    params = {"term": query, "entity": "software", "country": country, "limit": 5}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            {
                "id": r["trackId"],
                "name": r["trackName"],
                "developer": r["artistName"],
                "rating": round(r.get("averageUserRating", 0), 2),
                "rating_count": r.get("userRatingCount", 0),
            }
            for r in results
        ]
    except Exception:
        return []


def get_reviews(app_name: str, app_id: int, country: str = "cn", count: int = 100,
                language: str = "zh") -> list[dict]:
    """
    获取用户评论：优先取较大范围的近期 App Store 评论，RSS 无数据时再用 Tavily 搜索摘要兜底。

    痛点优先级由上层对评论主题的聚类覆盖量决定，不依赖各渠道不稳定的点赞数据。
    """
    reviews = _merge_reviews(_get_rss_reviews(app_id, country, count, "mostrecent", "recent"))
    if reviews:
        return reviews[:count]

    # RSS 无数据，用 Tavily 搜索
    return _get_tavily_reviews(app_name, count, language)


def _to_int(value) -> int:
    try:
        return int(value.get("label", 0)) if isinstance(value, dict) else int(value or 0)
    except (TypeError, ValueError):
        return 0


def _merge_reviews(reviews: list[dict]) -> list[dict]:
    """按评论文本去重，并保留同一评论中更完整的互动信息。"""
    merged = {}
    for review in reviews:
        text = review.get("text", "").strip()
        key = "".join(text.lower().split())
        if not key:
            continue
        existing = merged.get(key)
        if not existing:
            merged[key] = review
            continue
        existing["vote_sum"] = max(existing.get("vote_sum", 0), review.get("vote_sum", 0))
        existing["vote_count"] = max(existing.get("vote_count", 0), review.get("vote_count", 0))
        if existing.get("sample_source") != review.get("sample_source"):
            existing["sample_source"] = "both"
    return list(merged.values())


def _get_rss_reviews(app_id: int, country: str, count: int, sort_by: str,
                     sample_source: str) -> list[dict]:
    reviews = []
    max_pages = min(10, (count + 49) // 50)
    for page in range(1, max_pages + 1):
        url = (
            f"https://itunes.apple.com/{country}/rss/customerreviews/"
            f"page={page}/id={app_id}/sortby={sort_by}/json"
        )
        try:
            # Apple 的旧 RSS 偶发缓存空 feed；加缓存参数可避免把暂时空结果当成无评论。
            resp = requests.get(url, params={"_": int(time.time() * 1000)}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            break
        entries = data.get("feed", {}).get("entry", [])
        if not entries:
            break
        for entry in entries:
            if "im:rating" not in entry:
                continue
            body = entry.get("content", {})
            text = body.get("label", "") if isinstance(body, dict) else ""
            if text:
                reviews.append({
                    "text": text,
                    "rating": _to_int(entry.get("im:rating")),
                    "vote_sum": _to_int(entry.get("im:voteSum")),
                    "vote_count": _to_int(entry.get("im:voteCount")),
                    "updated": entry.get("updated", {}).get("label", ""),
                    "sample_source": sample_source,
                })
        if len(reviews) >= count:
            break
    return reviews[:count]


def _get_tavily_reviews(app_name: str, count: int, language: str = "zh") -> list[dict]:
    """用 Tavily 搜索真实用户评价，来源包括知乎、贴吧、应用市场等。"""
    try:
        from tavily import TavilyClient
        api_key = os.getenv("TAVILY_API_KEY", "")
        if not api_key:
            return []
        tavily = TavilyClient(api_key=api_key)

        queries = (
            [f"{app_name} user reviews pros cons", f"{app_name} app user feedback complaints"]
            if language == "en" else
            [f"{app_name} 使用体验 评价 优缺点", f"{app_name} app 用户反馈 吐槽"]
        )
        reviews = []
        for query in queries:
            results = tavily.search(
                query=query,
                search_depth="basic",
                max_results=10,
                include_raw_content=False
            )
            for r in results.get("results", []):
                content = r.get("content", "").strip()
                if content and len(content) > 30:
                    # 按句号分割，取有意义的段落
                    for chunk in content.split("。"):
                        chunk = chunk.strip()
                        if len(chunk) > 20:
                            reviews.append({
                                "text": chunk,
                                "rating": 0,
                                "vote_sum": 0,
                                "vote_count": 0,
                                "updated": "",
                                "sample_source": "web_search",
                            })
            if len(reviews) >= count:
                break

        return reviews[:count]
    except Exception:
        return []
