#!/usr/bin/env python3
"""
NLP Model Preloader (M3 / H-H)

下載 H-H NLP pipeline 用到的所有本地模型權重到 HF_HOME 指定的 cache
（見 .env 的 HF_HOME，預設 .cache/huggingface），讓正式環境開機前就把
權重準備好，不必等第一個使用者請求才觸發下載。

使用方式：
    cd backend
    uv run python scripts/preload_models.py

重跑一次也安全：huggingface_hub 命中 cache 就不會重新下載。
"""

import sys
from pathlib import Path

# 讓 scripts/ 可以 import 專案根目錄（backend/）下的模組
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 載入 backend/.env，取得 HF_HOME 等設定
try:
    from dotenv import load_dotenv

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass


def main() -> None:
    from chat.services import embedding, emotion
    from core.llm_provider import get_embeddings

    print(f"[1/3] 下載 embedding 模型：{embedding._MODEL_NAME}")
    embedding._get_model()

    print(f"[2/3] 下載情緒偵測模型：{emotion._MODEL_NAME}")
    emotion._get_pipeline()

    print("[3/3] 下載 RAG embedding 模型（EMBEDDING_MODEL，預設 all-MiniLM-L6-v2）")
    get_embeddings().embed_query("warmup")

    print("\n=== 模型權重已就緒 ===")


if __name__ == "__main__":
    main()
