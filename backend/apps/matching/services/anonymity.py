"""H-H 配對房的「表面匿名」名稱指派（見 CLAUDE.md「表面匿名、實質具名機制」）。

10 個匿名代號寫死在下面的 ANONYMOUS_IDS，不寫入資料庫、不快取——每次要用時
用 room_id 當種子即時算出來：同一間聊天室每次算出來的結果都一樣（同室內的
兩位參與者各自穩定拿到同一個匿名代號），不同聊天室之間彼此獨立、互不相關，
不會被拿來互相推論身分。

Godot 大廳的玩家顯示名稱用的是同一組代號（複製在 ../godot/Globals/AnonNames.gd，
指派方式不同——大廳人數不定，由 server 逐一發號）。改這裡的 ANONYMOUS_IDS 要
一併改那邊，否則同一位受試者在遊戲裡跟在聊天室裡會被叫成不同風格的名字。
"""

import random

ANONYMOUS_IDS = [
    "飽食海星",
    "噴水龍",
    "火箭龜",
    "風速貓",
    "香香泥",
    "千變怪",
    "七邊獸",
    "然後翁",
    "熊寶貝",
    "哥吉拉斯",
]


def assign_anonymous_ids(room_id: str, user_ids: list[int]) -> dict[int, str]:
    """回傳 {user_id: 匿名ID} 的對照表，同一間房內保證不重複。

    用 room_id 當亂數種子做不放回抽樣（random.Random(room_id).sample），
    對同一個 room_id 永遠給同一組結果，是純函式、不需要任何儲存/快取。
    user_ids 先排序過再跟抽樣結果配對，確保呼叫端傳入順序不影響結果
    （同一個 room_id + 同一組 user_id，不管呼叫時傳入順序為何，結果都一樣）。

    user_ids 數量不能超過 ANONYMOUS_IDS 的長度（目前 10）——H-H 配對房固定
    兩人一室用不到這麼多，這裡只是防呆。
    """
    if len(user_ids) > len(ANONYMOUS_IDS):
        raise ValueError(f"最多支援 {len(ANONYMOUS_IDS)} 位參與者的匿名 ID 指派。")

    rng = random.Random(room_id)
    picks = rng.sample(ANONYMOUS_IDS, k=len(user_ids))
    return dict(zip(sorted(user_ids), picks))
