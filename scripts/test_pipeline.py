#我是測試正式版請不要加我
import os
import sys
import psycopg
from dotenv import load_dotenv

from 品質篩選 import passes_quality_filter
from 去重檢查 import is_duplicate
from 觀點寫入 import write_dialogue_summary, write_viewpoint
from embedding import generate_embedding

load_dotenv()

# ──────────────────────────────────────────────
# 資料庫連線設定（沿用 backend 主資料庫的環境變數命名）
# ──────────────────────────────────────────────

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ.get("DB_NAME", "bridgeus"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


def get_connection():
    return psycopg.connect(**DB_CONFIG)


# ──────────────────────────────────────────────
# 測試資料
# ──────────────────────────────────────────────

GOOD_DIALOGUE = [
    {"role": "user", "content": "我認為核能發電在當前能源轉型過程中仍有其存在必要性，因為它的碳排放量相對低"},
    {"role": "user", "content": "但是核廢料的處理問題確實是一大隱憂，目前全球都還沒有完善的解決方案"},
    {"role": "user", "content": "不過相比煤炭發電，核能在發電過程中產生的空污明顯少很多"},
    {"role": "user", "content": "台灣地震頻繁也是一個需要考量的安全因素，這點我承認是核能的弱點"},
    {"role": "user", "content": "但從電力穩定性來看，再生能源目前還無法完全取代基載電力"},
    {"role": "user", "content": "所以我認為在儲能技術成熟之前，核能是必要的過渡選項"},
]

SHORT_DIALOGUE = [
    {"role": "user", "content": "核能不好"},
    {"role": "user", "content": "會爆炸"},
    {"role": "user", "content": "不要蓋"},
]

ATTACK_DIALOGUE = GOOD_DIALOGUE + [
    {"role": "user", "content": "你這個白痴根本不懂能源問題"},
]

FAKE_DIALOGUE_SUMMARY = {
    "dialogue_id": "9001",
    "topic_id": 1,
    "summary_text": "使用者討論了核能發電的優缺點，包含碳排放、核廢料處理與安全性等面向，最終認為核能是儲能技術成熟前的必要過渡選項。",
    "side_a_stance": "pro",
    "side_b_stance": "neutral",
    "quality_score": 0.82,
    "stance_shift_magnitude": 0.35,
}

FAKE_VIEWPOINT = {
    "summary_id": 1,
    "topic_id": 1,
    "dimension": "safety",
    "stance_direction": "pro",
    "user_input_text": "核能在低碳排放這個面向確實有其優勢，但安全疑慮不能忽視",
    "ai_response_text": "您提到了核能的兩個核心矛盾：低碳優勢與安全風險，這正是當前辯論的焦點",
    "viewpoint_summary": "使用者承認核能低碳優勢，但對安全性持保留態度",
    "source_message_ids": [101, 102],
    "composite_score": 0.74,
    "score_detail": {
        "semantic_dist": 0.73,
        "stance_shift": 0.81,
        "lexical_richness": 0.65
    }
}


# ──────────────────────────────────────────────
# 測試函式
# ──────────────────────────────────────────────

def test_connection():
    print("\n【第一關】環境連線")
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT version();")
        print("  [YES] PostgreSQL 連線成功：", cur.fetchone()[0][:40])
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
        result = cur.fetchone()
        if result:
            print("  [YES] pgvector 已安裝")
        else:
            print("  [NO] pgvector 未安裝，請執行：CREATE EXTENSION vector;")
        conn.close()
        return True
    except Exception as e:
        print("  [NO] 連線失敗：", e)
        return False


def test_quality_filter():
    print("\n【第二關】品質篩選（Step 1）")
    cases = [
        (passes_quality_filter(GOOD_DIALOGUE),   True,  "正常對話"),
        (passes_quality_filter(SHORT_DIALOGUE),  False, "對話太短"),
        (passes_quality_filter(ATTACK_DIALOGUE), False, "含攻擊性詞彙"),
    ]
    all_pass = True
    for got, expected, label in cases:
        ok = got == expected
        print(f"  {'[YES]' if ok else '[NO]'} {label}：預期 {expected}，實際 {got}")
        if not ok:
            all_pass = False
    return all_pass


def test_embedding():
    print("\n【第三關】Embedding 生成（Step 5）")
    try:
        emb = generate_embedding("核能發電對台灣能源安全的重要性")
        dim_ok = len(emb) == 384
        type_ok = isinstance(emb[0], float)
        print(f"  {'[YES]' if dim_ok else '[NO]'} 維度：{len(emb)}（預期 384）")
        print(f"  {'[YES]' if type_ok else '[NO]'} 型別：{type(emb[0]).__name__}（預期 float）")
        return dim_ok and type_ok
    except Exception as e:
        print("  [NO] Embedding 生成失敗：", e)
        return False


def test_dedup(conn):
    print("\n【第四關】去重檢查（Step 6）")
    cur = conn.cursor()
    summary_id = None
    try:
        cur.execute("""
            INSERT INTO summary_dialoguesummary
                (dialogue_id, topic_id, summary_text, side_a_stance, side_b_stance, created_at)
            VALUES ('[TEST_ONLY]-dedup', 99, '[TEST_ONLY] 去重測試摘要', 'pro', 'neutral', NOW())
            RETURNING id
        """)
        summary_id = cur.fetchone()[0]
        conn.commit()

        text_a = "核能發電的碳排放量相對低，是能源轉型的重要選項"
        emb_a = generate_embedding(text_a)
        cur.execute("""
            INSERT INTO summary_viewpointnode
                (summary_id, topic_id, dimension, stance_direction,
                 user_input_text, ai_response_text, viewpoint_summary,
                 source_message_ids, embedding, composite_score, score_detail,
                 citation_count, created_at)
            VALUES (%s, 99, 'safety', 'pro', %s, '[TEST_ONLY] ai response', '[TEST_ONLY] viewpoint summary',
                    '{}', %s::vector, 0, '{}', 0, NOW())
        """, (summary_id, text_a, emb_a))
        conn.commit()

        emb_similar = generate_embedding("核能的碳排放量比較低，對能源轉型有幫助")
        is_dup, _ = is_duplicate(conn, emb_similar, topic_id=99, dimension="safety")
        print(f"  {'[YES]' if is_dup else '[NO]'} 相似文本 → 重複偵測：{is_dup}（預期 True）")

        emb_diff = generate_embedding("太陽能板的光電轉換效率近年有顯著提升")
        is_dup2, _ = is_duplicate(conn, emb_diff, topic_id=99, dimension="safety")
        print(f"  {'[YES]' if not is_dup2 else '[NO]'} 不同文本 → 重複偵測：{is_dup2}（預期 False）")

        return is_dup and not is_dup2
    except Exception as e:
        print("  [NO] 去重測試失敗：", e)
        conn.rollback()
        return False
    finally:
        cur.execute("DELETE FROM summary_viewpointnode WHERE topic_id = 99")
        if summary_id:
            cur.execute("DELETE FROM summary_dialoguesummary WHERE id = %s", (summary_id,))
        conn.commit()


_PIPELINE_TEST_VIEWPOINT = {
    **FAKE_VIEWPOINT,
    "topic_id": 9999,
    "user_input_text": "[TEST_ONLY] 核能在低碳排放這個面向確實有其優勢，但安全疑慮不能忽視",
}


def test_full_pipeline(conn):
    print("\n【第五關】端對端整合（Step 1 → 5 → 6 → 7）")
    cur = conn.cursor()
    summary_id = None
    node_id = None
    try:
        cur.execute("DELETE FROM summary_viewpointnode WHERE user_input_text LIKE '[TEST_ONLY]%'")
        conn.commit()
        cur.execute("""
            INSERT INTO summary_dialoguesummary
                (dialogue_id, topic_id, summary_text, side_a_stance, side_b_stance, created_at)
            VALUES ('0', 1, '[TEST_ONLY] 端對端整合測試摘要', 'pro', 'neutral', NOW())
            RETURNING id
        """)
        summary_id = cur.fetchone()[0]
        conn.commit()

        viewpoint = {**_PIPELINE_TEST_VIEWPOINT, "summary_id": summary_id}

        written = write_viewpoint(conn, viewpoint)
        cur.execute("SELECT id, composite_score FROM summary_viewpointnode ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        node_id = row[0]
        print(f"  {'[YES]' if written and row else '[NO]'} 第一次寫入：id={row[0]}，score={row[1]}")

        write_viewpoint(conn, viewpoint)
        cur.execute("SELECT citation_count FROM summary_viewpointnode WHERE id = %s", (node_id,))
        citation = cur.fetchone()[0]
        print(f"  {'[YES]' if citation == 1 else '[NO]'} 第二次寫入觸發去重：citation_count={citation}（預期 1）")

        return written and citation == 1
    except Exception as e:
        print("  [NO] 整合測試失敗：", e)
        conn.rollback()
        return False
    finally:
        if node_id:
            cur.execute("DELETE FROM summary_viewpointnode WHERE id = %s", (node_id,))
        if summary_id:
            cur.execute("DELETE FROM summary_dialoguesummary WHERE id = %s", (summary_id,))
        conn.commit()


def test_write_dialogue_summary(conn):
    print("\n【第六關】寫入 dialogue_summary（Step 2）")
    cur = conn.cursor()
    summary_id = None
    try:
        passed = passes_quality_filter(GOOD_DIALOGUE)
        print(f"  {'[YES]' if passed else '[NO]'} 品質篩選通過：{passed}")
        if not passed:
            return False

        summary_id = write_dialogue_summary(conn, FAKE_DIALOGUE_SUMMARY)
        print(f"  [YES] 寫入成功：id={summary_id}")

        cur.execute("""
            SELECT dialogue_id, topic_id, side_a_stance, quality_score
            FROM summary_dialoguesummary WHERE id = %s
        """, (summary_id,))
        row = cur.fetchone()
        ok = (
            row is not None
            and row[0] == FAKE_DIALOGUE_SUMMARY["dialogue_id"]
            and row[1] == FAKE_DIALOGUE_SUMMARY["topic_id"]
            and row[2] == FAKE_DIALOGUE_SUMMARY["side_a_stance"]
        )
        print(f"  {'[YES]' if ok else '[NO]'} 資料驗證：dialogue_id={row[0]}, topic_id={row[1]}, side_a_stance={row[2]}, quality_score={row[3]}")
        return ok
    except Exception as e:
        print("  [NO] 寫入失敗：", e)
        conn.rollback()
        return False
    finally:
        if summary_id:
            cur.execute("DELETE FROM summary_dialoguesummary WHERE id = %s", (summary_id,))
            conn.commit()


# ──────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────

def main():
    print("=" * 50)
    print("BridgeUs 觀點知識庫 — 測試")
    print("=" * 50)

    results = {}

    results["連線"] = test_connection()
    if not results["連線"]:
        print("\n[NO] 資料庫連線失敗，後續測試中止")
        return

    print("\n[...] 預熱 Embedding 模型中...")
    generate_embedding("warmup")
    print("  [YES] 模型載入完成")

    results["品質篩選"] = test_quality_filter()
    results["Embedding"] = test_embedding()

    conn = get_connection()
    results["去重"] = test_dedup(conn)
    results["整合寫入"] = test_full_pipeline(conn)
    results["dialogue_summary 寫入"] = test_write_dialogue_summary(conn)
    conn.close()

    print("\n" + "=" * 50)
    print("測試結果總覽")
    print("=" * 50)
    all_ok = True
    for name, ok in results.items():
        print(f"  {'[YES]' if ok else '[NO]'} {name}")
        if not ok:
            all_ok = False
    print()
    if all_ok:
        print("[PASS] 全部通過，可以交給其他人對接了！")
        print("\n[...] 寫入示範資料到資料庫...")
        seed_test_data()
    else:
        print("[WARN] 有測試未通過，請先修復再對接。")


def seed_test_data():
    """將測試資料永久寫入資料庫（不清除），供其他人對接驗證用。"""
    print("=" * 50)
    print("寫入測試資料到資料庫")
    print("=" * 50)

    conn = get_connection()
    cur = conn.cursor()
    try:
        summary_id = write_dialogue_summary(conn, FAKE_DIALOGUE_SUMMARY)
        print(f"\n[YES] dialogue_summary 寫入成功：id={summary_id}")
        print(f"      dialogue_id={FAKE_DIALOGUE_SUMMARY['dialogue_id']}, topic_id={FAKE_DIALOGUE_SUMMARY['topic_id']}")
        print(f"      side_a={FAKE_DIALOGUE_SUMMARY['side_a_stance']}, quality_score={FAKE_DIALOGUE_SUMMARY['quality_score']}")

        print("\n[...] 載入 Embedding 模型中...")
        viewpoint = {**FAKE_VIEWPOINT, "summary_id": summary_id}
        written = write_viewpoint(conn, viewpoint)
        if written:
            cur.execute("SELECT id FROM summary_viewpointnode ORDER BY id DESC LIMIT 1")
            node_id = cur.fetchone()[0]
            print(f"\n[YES] viewpoint_node 寫入成功：id={node_id}")
            print(f"      summary_id={summary_id}, dimension={viewpoint['dimension']}, composite_score={viewpoint['composite_score']}")
        else:
            print("\n[WARN] viewpoint_node 偵測為重複，citation_count +1")

        print("\n" + "=" * 50)
        print(f"資料已保留在資料庫，dialogue_summary.id = {summary_id}")
        print("=" * 50)
    except Exception as e:
        print(f"\n[NO] 寫入失敗：{e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "seed":
        seed_test_data()
    else:
        main()
