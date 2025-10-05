import time
import random
import feedparser
import requests
import requests_cache
from bs4 import BeautifulSoup
import json
from datetime import datetime

FEED_URL = "https://finance.yahoo.com/news/rssindex"

# 1) Cache to avoid re-downloading articles we've already fetched
requests_cache.install_cache('http_cache', expire_after=60*60*6)  # 6 hours cache

# 2) Global limits
MIN_DELAY_SECONDS = 0.8   # minimum delay between requests
MAX_RETRIES = 5

# Optional: set a clear, real User-Agent
HEADERS = {
    "User-Agent": "MyFeedFetcher/1.0 (+youremail@example.com)"
}

def fetch_with_backoff(url):
    """Fetch URL with exponential backoff + Retry-After handling + caching."""
    session = requests.Session()
    session.headers.update(HEADERS)
    print(f"Fetching {url}...")

    backoff = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=15)
        except requests.RequestException as e:
            # network error: backoff and retry
            wait = backoff + random.uniform(0, 0.5)
            print(f"Network error: {e}. Backing off {wait:.1f}s (attempt {attempt})")
            time.sleep(wait)
            backoff *= 2
            continue

        if getattr(resp, "from_cache", False):
            # Cached -> return immediately
            return resp

        if resp.status_code == 200:
            return resp

        if resp.status_code == 429:
            # Honor Retry-After header if present
            ra = resp.headers.get("Retry-After")
            if ra:
                try:
                    wait = int(ra)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff

            wait = wait + random.uniform(0, 1.0)
            print(f"429 for {url}. Waiting {wait:.1f}s (attempt {attempt}).")
            time.sleep(wait)
            backoff *= 2
            continue

        if 500 <= resp.status_code < 600:
            wait = backoff + random.uniform(0, 0.5)
            print(f"Server error {resp.status_code}. Backing off {wait:.1f}s (attempt {attempt}).")
            time.sleep(wait)
            backoff *= 2
            continue

        print(f"Received status {resp.status_code} for {url}. Not retrying further.")
        return resp

    print(f"Exhausted retries for {url}. Returning last response (status {resp.status_code}).")
    return resp

def extract_article_text(html):
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("div", class_="caas-body") or soup.find("article") or soup.find("main")
    if not body:
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
    else:
        paragraphs = [p.get_text(strip=True) for p in body.find_all("p")]
    return "\n\n".join(paragraphs).strip()

def main():
    feed = feedparser.parse(FEED_URL)

    articles = []
    last_fetch_time = 0.0

    for entry in feed.entries:
        # Respect minimum delay between requests
        now = time.time()
        since = now - last_fetch_time
        if since < MIN_DELAY_SECONDS:
            time.sleep(MIN_DELAY_SECONDS - since)

        resp = fetch_with_backoff(entry.link)
        last_fetch_time = time.time()

        article_text = None
        if resp and resp.status_code == 200:
            article_text = extract_article_text(resp.text)
        if not article_text:
            article_text = entry.get("description", "")

        article_data = {
            "id": entry.get("id", entry.link),
            "title": entry.title,
            "link": entry.link,
            "published": entry.get("published"),
            "summary": entry.get("description", ""),
            "content": article_text
        }
        articles.append(article_data)

    # Build JSON with metadata
    feed_json = {
        "metadata": {
            "source": FEED_URL,
            "title": feed.feed.get("title", "Yahoo Finance News"),
            "link": feed.feed.get("link", "https://finance.yahoo.com/news"),
            "description": feed.feed.get("description", ""),
            "fetched_at": datetime.now().isoformat() + "Z"
        },
        "articles": articles
    }

    # Write to file
    with open("yahoo_finance_full_polite.json", "w", encoding="utf-8") as f:
        json.dump(feed_json, f, indent=2, ensure_ascii=False)

    print("✅ Done: yahoo_finance_full_polite.json")

if __name__ == "__main__":
    main()