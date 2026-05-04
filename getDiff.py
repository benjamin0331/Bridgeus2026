import json
from pathlib import Path


def load_json_list(path: Path):
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        for key in ("articles", "data", "items", "results", "documents"):
            if key in data and isinstance(data[key], list):
                return data[key]
        raise ValueError(f"JSON file {path} 不是文章列表，也沒有可用的列表欄位。")

    if not isinstance(data, list):
        raise ValueError(f"JSON file {path} 必須是列表格式。")

    return data


def get_metadata_url(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        metadata_url = metadata.get("url")
        if isinstance(metadata_url, str):
            return metadata_url.strip()
    return ""


def get_content(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    content = item.get("content")
    if isinstance(content, str):
        return content
    return ""


def build_diff(articles: list[dict], polear_articles: list[dict]) -> list[dict]:
    articles_urls = {
        get_metadata_url(article)
        for article in articles
        if get_metadata_url(article)
    }
    polear_urls = {
        get_metadata_url(article)
        for article in polear_articles
        if get_metadata_url(article)
    }

    diffs = []

    for article in articles:
        url = get_metadata_url(article)
        if url and url not in polear_urls:
            diffs.append({"content": get_content(article)})

    for article in polear_articles:
        url = get_metadata_url(article)
        if url and url not in articles_urls:
            diffs.append({"content": get_content(article)})

    return diffs


def main():
    base_folder = Path(__file__).resolve().parent
    articles_path = base_folder / "articles.json"
    polear_path = base_folder / "article_by_polear.json"
    diff_path = base_folder / "diff.json"

    if not articles_path.exists() or not polear_path.exists():
        raise FileNotFoundError(
            f"請確認 {articles_path.name} 與 {polear_path.name} 是否存在於 {base_folder}。"
        )

    articles = load_json_list(articles_path)
    polear_articles = load_json_list(polear_path)

    diffs = build_diff(articles, polear_articles)

    with diff_path.open("w", encoding="utf-8") as f:
        json.dump(diffs, f, ensure_ascii=False, indent=2)

    print(f"已建立 {diff_path.name}，共 {len(diffs)} 筆 URL 差異紀錄。")


if __name__ == "__main__":
    main()
