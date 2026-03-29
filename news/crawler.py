import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.parse
import unicodedata
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

import feedparser
import requests
import trafilatura
from googlenewsdecoder import gnewsdecoder
from requests.exceptions import HTTPError, RequestException

from env_utils import load_dotenv_file


load_dotenv_file()


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
SEED_ENV_VAR = "NEWS_CRAWLER_SEED"
DEBUG_ENV_VAR = "CRAWLER_DEBUG_INPUT"
DEFAULT_NEWS_CONTEXT = ""
DEFAULT_GEMINI_ATTEMPTS = 2
DEFAULT_GEMINI_WAIT_SECONDS = 10.0

def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    return normalized.strip()


def text_to_hex(text: str) -> str:
    return normalize_text(text).encode("utf-8").hex()


def debug_input_enabled() -> bool:
    return os.getenv(DEBUG_ENV_VAR, "").strip() in {"1", "true", "TRUE", "yes", "YES"}


def print_cache_debug(seed_keyword: str) -> None:
    if not debug_input_enabled():
        return

    seed_keyword = normalize_text(seed_keyword)
    print(f"DEBUG: seed='{seed_keyword}'")
    print(f"DEBUG: seed_hex={text_to_hex(seed_keyword)}")
    print("DEBUG: gemini_cache=disabled")


def looks_like_mojibake(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False

    if "?" in normalized:
        return True

    suspicious_markers = ["�", "Ã", "å", "ä", "é", "è", "ç", "æ", "ï", "ð"]
    if any(marker in normalized for marker in suspicious_markers):
        return True

    return False


def configure_stdio() -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def post_json_with_retry(
    url: str,
    payload: dict,
    attempts: int = DEFAULT_GEMINI_ATTEMPTS,
    base_wait_seconds: float = DEFAULT_GEMINI_WAIT_SECONDS,
) -> dict:
    last_error: Exception | None = None
    session = requests.Session()
    session.trust_env = False

    for attempt in range(1, attempts + 1):
        try:
            response = session.post(url, json=payload, timeout=60)
            response.raise_for_status()
            return response.json()
        except HTTPError as exc:
            last_error = exc
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 429 and attempt < attempts:
                wait_seconds = (base_wait_seconds * (2 ** (attempt - 1))) + random.random()
                print(f"[Gemini retry] rate limited, retrying in {wait_seconds:.1f}s ({attempt}/{attempts})")
                time.sleep(wait_seconds)
                continue
            raise RuntimeError(format_gemini_error(exc)) from exc
        except RequestException as exc:
            last_error = exc
            if attempt < attempts:
                wait_seconds = (base_wait_seconds * (2 ** (attempt - 1))) + random.random()
                print(f"[Gemini retry] request failed, retrying in {wait_seconds:.1f}s ({attempt}/{attempts})")
                time.sleep(wait_seconds)
                continue
            raise RuntimeError(format_gemini_error(exc)) from exc

    raise RuntimeError(format_gemini_error(last_error))


def format_gemini_error(error: Exception | None) -> str:
    if error is None:
        return "Gemini request failed"

    if isinstance(error, HTTPError):
        status_code = error.response.status_code if error.response is not None else "unknown"
        if status_code == 429:
            return "Gemini API returned HTTP 429 (rate limit or quota exceeded)"
        if status_code == 404:
            return "Gemini API returned HTTP 404 (model or API key unavailable for this endpoint)"
        return f"Gemini API returned HTTP {status_code}"

    message = str(error)
    if "127.0.0.1" in message and "proxy" in message.lower():
        return "Gemini request failed because of an invalid local proxy setting"

    return f"Gemini request failed: {message}"


def generate_keywords_with_gemini(
    topic: str,
    num_keywords: int = 5,
    model: str | None = None,
) -> list[str]:
    topic = normalize_text(topic)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("請先設定 GEMINI_API_KEY 環境變數")

    gemini_model = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    prompt = (
        f"請為主題「{topic}」產生 {num_keywords} 個繁體中文新聞搜尋關鍵詞。\n"
        "每行一個詞，不要編號，不要解釋，避免重複。"
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{gemini_model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4},
    }

    data = post_json_with_retry(url, payload)
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "\n".join(part.get("text", "") for part in parts).strip()

    keywords: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        normalized = normalize_text(re.sub(r"^[\-\*\d\.\)\s]+", "", line))
        if normalized and normalized not in seen:
            seen.add(normalized)
            keywords.append(normalized)

    return keywords[:num_keywords]


def make_id(*parts: str) -> str:
    raw = "||".join([part for part in parts if part])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def save_json(path: str | Path, data) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_json(path: str | Path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def slugify_filename(text: str, max_len: int = 40) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text.strip())
    safe = "_".join(part for part in safe.split("_") if part)
    return safe[:max_len].rstrip("_") or "keyword"


def merge_keywords(seed_keyword: str, keywords: list[str], max_keywords: int) -> list[str]:
    seed_keyword = normalize_text(seed_keyword)
    merged: list[str] = []
    seen: set[str] = set()
    for item in [seed_keyword, *keywords]:
        normalized = normalize_text(re.sub(r"\s+", " ", item))
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)
    return merged[:max_keywords]


def expand_keywords(
    seed_keyword: str,
    max_keywords: int = 5,
    gemini_model: str | None = None,
) -> tuple[list[str], str]:
    seed_keyword = normalize_text(seed_keyword)
    print_cache_debug(seed_keyword)

    try:
        keywords = generate_keywords_with_gemini(
            seed_keyword,
            num_keywords=max_keywords,
            model=gemini_model,
        )
    except Exception as exc:
        print(f"[Gemini fallback] {exc}")
        return [seed_keyword], "seed_only"

    return merge_keywords(seed_keyword, keywords, max_keywords), "gemini"


def google_news_rss_search(
    query: str,
    lang: str | None = None,
    country: str | None = None,
    max_items: int = 10,
) -> list[dict]:
    query = normalize_text(query)
    scoped_query = normalize_text(" ".join(part for part in (query, DEFAULT_NEWS_CONTEXT) if part))
    encoded_query = urllib.parse.quote(scoped_query)

    query_params = {"q": encoded_query}
    if lang:
        query_params["hl"] = lang
    if country:
        query_params["gl"] = country
    if lang and country:
        query_params["ceid"] = f"{country}:{lang}"

    rss_url = "https://news.google.com/rss/search?" + "&".join(
        f"{key}={value}" for key, value in query_params.items()
    )
    feed = feedparser.parse(rss_url)
    items = []

    for entry in feed.entries[:max_items]:
        items.append(
            {
                "title": entry.get("title", "").strip(),
                "google_news_url": entry.get("link", "").strip(),
                "published": entry.get("published", "").strip(),
                "summary": re.sub(r"<.*?>", "", entry.get("summary", "")).strip(),
            }
        )

    return items


def decode_google_news_url(google_news_url: str) -> str:
    try:
        decoded = gnewsdecoder(google_news_url)
        if isinstance(decoded, dict):
            return decoded.get("decoded_url") or decoded.get("url") or google_news_url
        return google_news_url
    except Exception:
        return google_news_url


def fetch_article_content(url: str) -> tuple[str | None, str | None]:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None, None

        content = trafilatura.extract(
            downloaded,
            output_format="txt",
            include_comments=False,
            include_tables=False,
            include_links=False,
            favor_precision=True,
        )
        metadata = trafilatura.extract_metadata(downloaded)
        source = metadata.sitename if metadata else None
        return content, source
    except Exception:
        return None, None


def crawl_news_by_keyword(
    seed_keyword: str,
    output_dir: str = "output",
    max_expanded_keywords: int = 5,
    max_news_per_keyword: int = 5,
    max_total_results: int | None = None,
    sleep_sec: float = 1.0,
    gemini_model: str | None = None,
) -> list[dict]:
    seed_keyword = normalize_text(seed_keyword)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    keyword_slug = slugify_filename(seed_keyword)
    keywords_file = output_path / f"{keyword_slug}_{timestamp}_expanded_keywords.json"
    news_file = output_path / f"{keyword_slug}_{timestamp}_news_results.json"

    expanded_keywords, expansion_source = expand_keywords(
        seed_keyword,
        max_keywords=max_expanded_keywords,
        gemini_model=gemini_model,
    )

    save_json(
        keywords_file,
        {
            "seed_keyword": seed_keyword,
            "expansion_source": expansion_source,
            "expanded_keywords": expanded_keywords,
        },
    )

    print(f"\n輸入關鍵詞：{seed_keyword}")
    print(f"擴詞來源：{expansion_source}")
    print("\n展開後關鍵詞：")
    for index, keyword in enumerate(expanded_keywords, 1):
        print(f"{index}. {keyword}")

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    results: list[dict] = []

    for keyword in expanded_keywords:
        if max_total_results is not None and len(results) >= max_total_results:
            break

        print(f"\n[搜尋] {keyword}")
        news_items = google_news_rss_search(keyword, max_items=max_news_per_keyword)

        for item in news_items:
            if max_total_results is not None and len(results) >= max_total_results:
                break

            title = item["title"]
            google_news_url = item["google_news_url"]
            if not title or not google_news_url:
                continue

            real_url = decode_google_news_url(google_news_url)
            if real_url in seen_urls or title in seen_titles:
                continue

            seen_urls.add(real_url)
            seen_titles.add(title)

            print(f"  新聞：{title}")
            content, source = fetch_article_content(real_url)

            if not content or len(content.strip()) < 100:
                print("   -> 內容不足，略過")
                continue

            results.append(
                {
                    "id": make_id(seed_keyword, keyword, real_url, title),
                    "document": title,
                    "content": content,
                    "metadata": {
                        "seed_keyword": seed_keyword,
                        "expanded_keyword": keyword,
                        "expansion_source": expansion_source,
                        "url": real_url,
                        "google_news_url": google_news_url,
                        "published": item["published"],
                        "source": source or "unknown",
                        "summary": item["summary"],
                        "crawled_at": datetime.now().isoformat(timespec="seconds"),
                    },
                }
            )
            time.sleep(sleep_sec)

    save_json(news_file, results)

    print(f"\n成功擷取 {len(results)} 筆新聞")
    print(f"關鍵詞輸出：{keywords_file.resolve()}")
    print(f"新聞輸出：{news_file.resolve()}")

    return results


def parse_cli_args():
    parser = argparse.ArgumentParser(description="Google News crawler with Gemini keyword expansion")
    parser.add_argument("seed", nargs="*", help="輸入關鍵詞")
    parser.add_argument("--output-dir", default="output", help="輸出目錄")
    parser.add_argument("--max-expanded-keywords", type=int, default=10, help="展開關鍵詞上限")
    parser.add_argument("--max-news-per-keyword", type=int, default=10, help="每個關鍵詞抓取新聞上限")
    parser.add_argument("--max-total-results", type=int, default=None, help="總新聞數上限")
    parser.add_argument("--sleep-sec", type=float, default=1.5, help="每篇文章間的等待秒數")
    parser.add_argument("--gemini-model", default=None, help="覆蓋預設 Gemini model")
    return parser.parse_args()


def resolve_seed(args) -> str:
    env_seed = normalize_text(os.getenv(SEED_ENV_VAR, ""))
    if env_seed:
        return env_seed

    if args.seed:
        seed = normalize_text(" ".join(normalize_text(part) for part in args.seed if normalize_text(part)))
        if looks_like_mojibake(seed):
            print("偵測到命令列關鍵詞可能有編碼問題，建議改用 run_crawler.ps1 執行。")
            print("範例：powershell -ExecutionPolicy Bypass -File .\\run_crawler.ps1 台灣重啟核電")
        return seed

    return normalize_text(input("請輸入關鍵詞："))


if __name__ == "__main__":
    configure_stdio()
    args = parse_cli_args()
    seed = resolve_seed(args)

    crawl_news_by_keyword(
        seed_keyword=seed,
        output_dir=args.output_dir,
        max_expanded_keywords=args.max_expanded_keywords,
        max_news_per_keyword=args.max_news_per_keyword,
        max_total_results=args.max_total_results,
        sleep_sec=args.sleep_sec,
        gemini_model=args.gemini_model,
    )
