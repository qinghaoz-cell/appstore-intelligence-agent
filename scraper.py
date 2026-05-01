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


def get_reviews(app_name: str, app_id: int, country: str = "cn", count: int = 150) -> list[str]:
    """
    Fetch App Store reviews via iTunes RSS feed (supports all countries,
    no authentication needed). Returns up to `count` review strings.
    Each RSS page has up to 50 reviews; we page through up to 10 pages.
    """
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
            # First entry is app metadata, not a review
            if "im:rating" not in entry:
                continue
            body = entry.get("content", {})
            text = body.get("label", "") if isinstance(body, dict) else ""
            if text:
                reviews.append(text)

        if len(reviews) >= count:
            break

    return reviews[:count]
