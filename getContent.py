import csv
import json
from pathlib import Path


def load_articles(json_path: Path):
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        # 如果 JSON 是單一物件，嘗試從常見欄位取出列表
        for key in ("articles", "data", "items", "results", "documents"):
            if key in data and isinstance(data[key], list):
                return data[key]
        raise ValueError(f"JSON file {json_path} 不是文章列表，也沒有可用的列表欄位。")

    if not isinstance(data, list):
        raise ValueError(f"JSON file {json_path} 必須是列表格式。")

    return data


def write_article_contents(articles, csv_path: Path):
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csvfile:
        writer = csv.writer(csvfile)
        for item in articles:
            if not isinstance(item, dict):
                continue
            content = item.get("content", "")
            writer.writerow([content])


def main():
    base_folder = Path(__file__).resolve().parent
    source_json = base_folder / "article_by_polear.json"
    target_csv = base_folder / "articlecontents.csv"

    if not source_json.exists():
        raise FileNotFoundError(f"找不到 {source_json}，請確認檔案是否存在。")

    articles = load_articles(source_json)
    write_article_contents(articles, target_csv)
    print(f"已從 {source_json.name} 讀取 {len(articles)} 筆資料，並寫入 {target_csv.name}。")


if __name__ == "__main__":
    main()
