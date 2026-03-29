import json
import os
import sys

sys.dont_write_bytecode = True

import requests

from env_utils import load_dotenv_file


load_dotenv_file()


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def generate_keywords(topic, num_keywords=30):
    if not GEMINI_API_KEY:
        raise ValueError("請先設定環境變數 GEMINI_API_KEY")

    prompt = f"""
請根據主題「{topic}」生成 {num_keywords} 個相關關鍵詞。

要求：
1. 與主題高度相關
2. 適合新聞搜尋與網路爬蟲
3. 包含政策、技術、安全、社會議題、產業等面向
4. 只輸出關鍵詞
5. 一行一個
6. 不要編號
"""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }

    response = requests.post(url, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()

    parts = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    text = "\n".join(part.get("text", "") for part in parts).strip()

    keywords = [k.strip() for k in text.split("\n") if k.strip()]
    return keywords


def save_to_json(topic, keywords, output_file="keywords.json"):
    data = {
        "topic": topic,
        "keywords": keywords
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"JSON 已輸出：{output_file}")


if __name__ == "__main__":
    topic = input("請輸入主題：").strip()
    keywords = generate_keywords(topic)

    print("\nGemini 生成關鍵詞：\n")
    for k in keywords:
        print("-", k)

    print("\nPython list：\n")
    print(keywords)

    save_to_json(topic, keywords)
