#!/usr/bin/env python3
"""
Knowledge Base Builder (M3)

將 JSON 格式的知識文件切片後寫入 ChromaDB，
供 DialogueAgent 的 RAG 鏈檢索使用。

使用方式：
    python scripts/build_knowledge_base.py --collection nuclear_energy_news
    python scripts/build_knowledge_base.py --collection nuclear_energy_news --data-dir data/nuclear
    python scripts/build_knowledge_base.py --collection nuclear_energy_news --chunk-size 500

Owner: 葉錦諦 (Data Engineering)
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 讓 scripts/ 可以 import backend/ 下的 core 模組
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# 載入 .env（優先找 backend/.env，其次找專案根目錄 .env）
try:
    from dotenv import load_dotenv
    for env_path in [BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env"]:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_core.documents import Document

from core.llm_provider import get_embeddings


def load_json_documents(data_dir: Path) -> list[Document]:
    """
    從目錄中讀取所有 .json 檔案，轉換為 LangChain Document 物件。

    JSON 格式支援兩種：
    1. 單一物件：{"title": "...", "content": "...", "source": "..."}
    2. 物件陣列：[{"title": "...", "content": "..."}, ...]
    """
    documents = []
    json_files = list(data_dir.glob("*.json"))

    if not json_files:
        print(f"[警告] 在 {data_dir} 找不到任何 .json 檔案")
        return documents

    for json_path in json_files:
        print(f"  載入: {json_path.name}")
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        # 統一處理成列表
        items = data if isinstance(data, list) else [data]

        for item in items:
            content = item.get("content", "")
            if not content.strip():
                continue

            # 支援 source/title 直接在頂層，或 nested 在 metadata 欄位內
            nested = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
            metadata = {
                "source": item.get("source") or nested.get("source", json_path.name),
                "title": item.get("title") or item.get("document", ""),
                "collection": item.get("collection") or nested.get("type", ""),
            }
            documents.append(Document(page_content=content, metadata=metadata))

    print(f"  共載入 {len(documents)} 篇文件")
    return documents


def split_documents(
    documents: list[Document],
    chunk_size: int,
    chunk_overlap: int,
) -> list[Document]:
    """將文件切成固定大小的 chunk，保留 metadata。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    print(f"  切片完成：{len(documents)} 篇 → {len(chunks)} 個 chunk")
    return chunks


def build_collection(
    chunks: list[Document],
    collection_name: str,
    chroma_dir: str,
) -> None:
    """將 chunk 寫入 ChromaDB collection（若 collection 已存在則覆蓋）。"""
    embeddings = get_embeddings()
    print(f"  建立 ChromaDB collection: {collection_name}")
    print(f"  資料目錄: {chroma_dir}")

    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=collection_name,
        persist_directory=chroma_dir,
    )
    print(f"  完成：已寫入 {len(chunks)} 個向量至 '{collection_name}'")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="將 JSON 知識文件建立成 ChromaDB collection",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--collection",
        required=True,
        help="ChromaDB collection 名稱（例如 nuclear_energy_news）",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="JSON 資料來源目錄（預設：data/<collection>）",
    )
    parser.add_argument(
        "--chroma-dir",
        default=os.getenv("CHROMA_PERSIST_DIR", "./chroma_data"),
        help="ChromaDB 持久化目錄",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=400,
        help="每個 chunk 的最大字元數",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=50,
        help="相鄰 chunk 的重疊字元數",
    )
    args = parser.parse_args()

    # 決定資料目錄
    data_dir = Path(args.data_dir) if args.data_dir else Path("data") / args.collection
    if not data_dir.exists():
        print(f"[錯誤] 資料目錄不存在：{data_dir}")
        print(f"       請建立目錄並放入 .json 檔案後再執行")
        sys.exit(1)

    print(f"=== 建立知識庫：{args.collection} ===")
    print(f"[1/3] 載入 JSON 文件...")
    documents = load_json_documents(data_dir)
    if not documents:
        print("[錯誤] 沒有可用的文件，中止執行")
        sys.exit(1)

    print(f"[2/3] 切片文件...")
    chunks = split_documents(documents, args.chunk_size, args.chunk_overlap)

    print(f"[3/3] 寫入 ChromaDB...")
    build_collection(chunks, args.collection, args.chroma_dir)

    print(f"\n=== 完成 ===")
    print(f"執行以下指令驗證：")
    print(
        f"  python -c \"from apps.matching.services.ai_agent import DialogueAgent; "
        f"a = DialogueAgent('{args.collection}'); print(a.respond('請介紹這個議題'))\""
    )


if __name__ == "__main__":
    main()
