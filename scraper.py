import os
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


def get_reviews(app_name: str, app_id: int, country: str = "cn", count: int = 100) -> list[str]:
    """
    获取用户评论：优先尝试 iTunes RSS Feed，若无数据则用 Tavily 搜索真实用户评价。
    """
    # 先试 RSS Feed
    reviews = _get_rss_reviews(app_id, country, count)
    if reviews:
        return reviews

    # RSS 无数据，用 Tavily 搜索
    return _get_tavily_reviews(app_name, count)


def _get_rss_reviews(app_id: int, country: str, count: int) -> list[str]:
    reviews = []
    max_pages = min(10, (count // 50) + 1)
    for page in range(1, max_pages + 1):
        url = (
            f"https://itunes.apple.com/{country}/rss/customerreviews/"
            f"page={page}/id={app_id}/sortby=mosthelpful/json"
        )
        try:
            resp = requests.get(url, timeout=10)
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
                reviews.append(text)
        if len(reviews) >= count:
            break
    return reviews[:count]


def _get_tavily_reviews(app_name: str, count: int) -> list[str]:
    """用 Tavily 搜索真实用户评价，来源包括知乎、贴吧、应用市场等。"""
    try:
        from tavily import TavilyClient
        api_key = os.getenv("TAVILY_API_KEY", "")
        if not api_key:
            return []
        tavily = TavilyClient(api_key=api_key)

        queries = [
            f"{app_name} 使用体验 评价 优缺点",
            f"{app_name} app 用户反馈 吐槽",
        ]
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
                            reviews.append(chunk)
            if len(reviews) >= count:
                break

        return reviews[:count]
    except Exception:
        return []
