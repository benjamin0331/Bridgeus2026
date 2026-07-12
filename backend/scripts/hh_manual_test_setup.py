"""
H-H 手動測試資料建立腳本。
執行：cd backend && uv run python scripts/hh_manual_test_setup.py

建立兩個測試用戶 + 一個 ACTIVE Conversation，印出 WebSocket 連線指令。
"""

import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "take_a_bridge.settings")
django.setup()

import numpy as np
from django.contrib.auth import get_user_model
from django.utils import timezone

from chat.models import Conversation, Message

User = get_user_model()

# ── 建立 / 取得測試用戶 ──────────────────────────────────────────────────────

alice, _ = User.objects.get_or_create(username="hh_alice", defaults={"password": "x"})
alice.set_password("test1234")
alice.save()

bob, _ = User.objects.get_or_create(username="hh_bob", defaults={"password": "x"})
bob.set_password("test1234")
bob.save()

# ── 建立對話（每次執行都建新的，方便重複測試）────────────────────────────────

# 用隨機向量模擬「問卷初始立場 embedding」（M2/M3 整合完成前的測試用）
rng = np.random.default_rng(42)
alice_initial = rng.random(384).tolist()
bob_initial   = rng.random(384).tolist()

conv = Conversation.objects.create(
    topic_id=1,
    user_a=alice,
    user_b=bob,
    session_number=1,
    status=Conversation.Status.ACTIVE,
    started_at=timezone.now(),
    user_a_initial_embedding=alice_initial,
    user_b_initial_embedding=bob_initial,
)

# ── 預埋訊息（含 embedding），確保 drift 觸發時有資料可算）────────────────────
# 模擬雙方各發 3 則，embedding 用隨機向量代替實際 NLP 結果

seed_contents = [
    ("核能是穩定的基載電力來源，可以補足再生能源的間歇性缺口。", alice),
    ("我認為台灣應該認真評估核能延役的可能性，能源安全很重要。", alice),
    ("廢核之後天然氣依賴增加，碳排放反而上升，這不是好的政策。", alice),
    ("核能有安全風險，福島事件就是前車之鑑，我們不能輕忽。", bob),
    ("再生能源技術進步很快，加上儲能系統，完全可以取代核能。", bob),
    ("核廢料的處置問題至今未解，這是留給下一代的負擔。", bob),
]

seed_rng = np.random.default_rng(99)
for content, sender in seed_contents:
    Message.objects.create(
        conversation=conv,
        sender=sender,
        content=content,
        embedding=seed_rng.random(384).tolist(),
    )

print(f"  預埋 {len(seed_contents)} 則訊息（含 embedding）完成")

# ── 印出測試資訊 ──────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print("H-H 手動測試資料建立完成")
print("=" * 60)
print(f"  conversation_id : {conv.id}")
print(f"  alice  user_id  : {alice.id}  (username: hh_alice)")
print(f"  bob    user_id  : {bob.id}  (username: hh_bob)")
print()
print("瀏覽器 Tab 1（Alice）貼入以下 JS：")
print(f"""
const ws = new WebSocket("ws://localhost:8000/ws/hh/{conv.id}/?user_id={alice.id}");
ws.onmessage = e => console.log("[RECV]", JSON.parse(e.data));
ws.onopen    = () => console.log("[OPEN] Alice connected");
ws.onclose   = e => console.log("[CLOSE]", e.code);
// 發送訊息：ws.send(JSON.stringify({{content: "哈囉，我支持核能！"}}))
// 結束對話：ws.send(JSON.stringify({{type: "end_session"}}))
""")
print("瀏覽器 Tab 2（Bob）貼入以下 JS：")
print(f"""
const ws = new WebSocket("ws://localhost:8000/ws/hh/{conv.id}/?user_id={bob.id}");
ws.onmessage = e => console.log("[RECV]", JSON.parse(e.data));
ws.onopen    = () => console.log("[OPEN] Bob connected");
ws.onclose   = e => console.log("[CLOSE]", e.code);
// 發送訊息：ws.send(JSON.stringify({{content: "我反對，核能有安全風險。"}}))
""")
print("=" * 60)
print(f"刪除此次測試對話：Conversation.objects.get(id={conv.id}).delete()")
print("=" * 60 + "\n")
