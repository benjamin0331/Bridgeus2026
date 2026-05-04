import json
import os
import sys


def extract_urls(data):
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() == "url" and isinstance(value, str):
                yield value
            else:
                yield from extract_urls(value)
    elif isinstance(data, list):
        for item in data:
            yield from extract_urls(item)


def main():
    filename = sys.argv[1] if len(sys.argv) > 1 else "article_by_polear.json"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, filename)

    if not os.path.exists(file_path):
        print(f"檔案不存在: {file_path}")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    urls = list(extract_urls(data))
    if not urls:
        print("找不到任何 URL。")
        return
    i = 0
    for url in urls:
        print(f"URL {i}: {url}")
        i += 1


if __name__ == "__main__":
    main()
