import os
import re
import json
import time
from pathlib import Path
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup

BASE = "https://law.moj.gov.tw"
SEARCH_URL = BASE + "/Law/LawSearchResult.aspx?ty=ONEBAR&kw={kw}&sSearch="
HEADERS = {
    "User-Agent": "Mozilla/5.0"
}
OUT_DIR = Path("law_json_output")
OUT_DIR.mkdir(exist_ok=True)

SLEEP_SEC = 2.0  # 政府站建議不要打太快


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    time.sleep(SLEEP_SEC)
    return r.text


def search_laws(keyword: str) -> list[dict]:
    """
    只抓「中央法規 > 法規名稱」結果
    回傳:
    [
      {
        "law_name": "...",
        "law_url": "...",
        "pcode": "..."
      }
    ]
    """
    url = SEARCH_URL.format(kw=quote(keyword))
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    results = []
    seen = set()

    # 搜尋所有可能連到法規全文頁的連結
    for a in soup.select('a[href*="LawAll.aspx?pcode="]'):
        href = a.get("href", "")
        full_url = urljoin(BASE + "/", href)

        # 只保留法規名稱主連結，排除 EN 等雜項
        text = clean_text(a.get_text(" ", strip=True))
        if not text or text == "EN":
            continue

        m = re.search(r"pcode=([A-Z0-9]+)", full_url)
        if not m:
            continue

        pcode = m.group(1)
        key = (pcode, text)
        if key in seen:
            continue
        seen.add(key)

        results.append({
            "law_name": text,
            "law_url": full_url,
            "pcode": pcode
        })

    # 有些搜尋頁也可能出現附件或其他重複連結，這裡再去重一次（依 pcode）
    dedup = {}
    for item in results:
        dedup[item["pcode"]] = item

    return list(dedup.values())


def parse_law_page(law_url: str, keyword: str) -> dict:
    """
    把單一法規頁解析成結構化資料
    """
    html = fetch_html(law_url)
    soup = BeautifulSoup(html, "html.parser")

    page_text = soup.get_text("\n", strip=True)

    # 法規名稱
    law_name = ""
    m = re.search(r"法規名稱：\s*(.+)", page_text)
    if m:
        law_name = clean_text(m.group(1)).split("EN")[0].strip()

    # 修正日期
    amend_date = ""
    m = re.search(r"修正日期：\s*(.+)", page_text)
    if m:
        amend_date = clean_text(m.group(1))

    # 法規類別
    category = ""
    m = re.search(r"法規類別：\s*(.+)", page_text)
    if m:
        category = clean_text(m.group(1))

    # 條文擷取
    # 網頁文字通常長這樣：
    # 第 1 條
    # 內容...
    # 第 2 條
    # 內容...
    lines = [clean_text(x) for x in page_text.splitlines()]
    lines = [x for x in lines if x]

    articles = []
    current_article_no = None
    current_content = []

    article_pat = re.compile(r"^第\s*[0-9一二三四五六七八九十百千零○]+\s*條(?:之\s*[0-9一二三四五六七八九十百千零○-]+)?$")

    for line in lines:
        if article_pat.match(line):
            if current_article_no:
                articles.append({
                    "article_no": current_article_no,
                    "content": clean_text("\n".join(current_content))
                })
            current_article_no = line
            current_content = []
        else:
            if current_article_no:
                current_content.append(line)

    if current_article_no:
        articles.append({
            "article_no": current_article_no,
            "content": clean_text("\n".join(current_content))
        })

    # 取 pcode
    pcode_match = re.search(r"pcode=([A-Z0-9]+)", law_url)
    pcode = pcode_match.group(1) if pcode_match else ""

    return {
        "keyword": keyword,
        "law_name": law_name,
        "pcode": pcode,
        "law_url": law_url,
        "amend_date": amend_date,
        "category": category,
        "article_count": len(articles),
        "articles": articles
    }


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:120]


def main():
    keyword = input("請輸入關鍵詞：").strip()
    if not keyword:
        print("關鍵詞不可空白")
        return

    laws = search_laws(keyword)
    print(f"找到 {len(laws)} 部法規")

    all_data = []

    for i, law in enumerate(laws, start=1):
        print(f"[{i}/{len(laws)}] 解析：{law['law_name']}")

        try:
            data = parse_law_page(law["law_url"], keyword)
            all_data.append(data)

            filename = f"{safe_filename(data['law_name'] or law['pcode'])}.json"
            out_path = OUT_DIR / filename

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"  失敗：{law['law_name']} -> {e}")

    all_path = OUT_DIR / "all_laws.json"
    with open(all_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"\n完成，輸出資料夾：{OUT_DIR.resolve()}")
    print(f"合併檔案：{all_path.resolve()}")


if __name__ == "__main__":
    main()