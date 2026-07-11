# Part 4a — `views.py` service extraction move map

本文件只規劃機械搬移，不搬 model、不改 URL、不重新命名函式，也不改函式內容。盤點基準為目前 `backend/api/views.py` 的 36 個 top-level helper（35 個 `_...` 函式，以及 `get_dialogue_agent`）：35 個搬入四個 service 模組，1 個保留在 view adapter。

## 函式 → 目標模組對照表

「跨模組介面」表示搬移後會被 `views.py`、`consumers.py` 或另一個 service import；「模組內部」表示只由同一個目標模組中的函式呼叫。函式名稱與內容一律保持不變。

### `api/services/dialogue_session.py`

| 來源 | 函式 | 職責 | 搬移後介面 |
|---|---|---|---|
| `views.py:64` | `_session_cache_key` | session cache key | 模組內部 |
| `views.py:68` | `_cache_dialogue_session_record` | cache 寫入與 TTL | 跨模組介面 |
| `views.py:76` | `_rebuild_session_state_from_turns` | 從持久化 turns 重建 session state | 模組內部 |
| `views.py:99` | `_dialogue_session_cache_payload_from_record` | DB record → cache payload | 跨模組介面 |
| `views.py:117` | `_persist_dialogue_session_record` | session record 持久化 | 跨模組介面 |
| `views.py:139` | `_restore_dialogue_session_record_for_user` | cache/DB restore | 跨模組介面 |
| `views.py:165` | `_dialogue_session_response_payload` | restore response payload | 跨模組介面 |
| `views.py:199` | `_update_ai_session_stance_drift` | AI session stance drift 更新 wrapper | 跨模組介面 |
| `views.py:347` | `_build_topic_config` | 問卷結果 → dialogue session topic/config payload | 跨模組介面 |
| `views.py:706` | `_get_dialogue_runtime` | lazy 載入 dialogue runtime classes | 跨模組介面 |
| `views.py:717` | `get_dialogue_agent` | collection-scoped agent cache/factory | 跨模組介面 |

### `api/services/stance_scoring.py`

| 來源 | 函式 | 職責 | 搬移後介面 |
|---|---|---|---|
| `views.py:218` | `_get_survey_scoring_config` | 正反向題、門檻與開放題 mapping | 模組內部 |
| `views.py:253` | `_get_open_answer` | 依 code/id 取得單題開放題答案 | 模組內部 |
| `views.py:267` | `_compute_user_stance_score` | Likert 反向計分與平均 | 跨模組介面 |
| `views.py:300` | `_resolve_stance_category` | score → support/neutral/oppose | 跨模組介面 |
| `views.py:310` | `_resolve_stances` | category → user/agent stance labels | 跨模組介面 |
| `views.py:329` | `_resolve_open_answers` | 開放題答案正規化 | 跨模組介面 |

`_build_topic_config` 留在 `dialogue_session.py`，因為它產出的是建立 session 所需的完整 payload；其計分、分類與開放題解析仍委派給 `stance_scoring.py`。這也讓 `DEFAULT_DIALOGUE_COLLECTION` 與 `_persist_dialogue_session_record` 保持在同一模組。

### `api/services/history.py`

| 來源 | 函式 | 職責 | 搬移後介面 |
|---|---|---|---|
| `views.py:545` | `_semantic_tree_root_name` | match topic → semantic-tree root name | 跨模組介面 |
| `views.py:549` | `_semantic_tree_root_name_for_topic_id` | topic id → semantic-tree root name | 跨模組介面 |
| `views.py:567` | `_get_history_ai_record_for_user` | 取得使用者 AI history record | 跨模組介面 |
| `views.py:578` | `_history_ai_turns` | AI turns queryset | 模組內部 |
| `views.py:585` | `_history_ai_messages` | AI turns → history messages | 模組內部 |
| `views.py:613` | `_history_match_messages` | match messages → history messages | 模組內部 |
| `views.py:630` | `_history_ai_summary` | AI history list item | 跨模組介面 |
| `views.py:652` | `_history_match_summary` | match history list item | 跨模組介面 |
| `views.py:672` | `_history_ai_detail` | AI history detail + semantic tree | 跨模組介面 |
| `views.py:689` | `_history_match_detail` | match history detail + semantic tree | 跨模組介面 |

兩個 semantic-tree root helper 放在 `history.py`，因為它們是 history summary/detail 與 semantic-tree payload 組裝的共用名稱解析；live semantic-tree views 也從此模組 import。不要讓 `history.py` 回頭 import `api.views`。

### `api/services/room_state.py`

| 來源 | 函式 | 職責 | 搬移後介面 |
|---|---|---|---|
| `views.py:419` | `_get_other_user` | 解析 room 對方 participant | 模組內部 |
| `views.py:423` | `_match_presence_fields` | presence/absence payload | 模組內部 |
| `views.py:446` | `_build_matching_state_payload` | matching state payload | 跨模組介面 |
| `views.py:481` | `_room_match_state_status` | room status → API status | 模組內部 |
| `views.py:487` | `_get_latest_room_stance_drift` | 最新 room stance drift | 模組內部 |
| `views.py:502` | `_build_room_messages_payload` | room messages + state payload | 跨模組介面 |
| `views.py:518` | `_get_room_match_for_user` | user-scoped room lookup | 跨模組介面 |
| `views.py:527` | `_touch_room_match_for_user_activity` | presence/touch/idle/absence checks | 跨模組介面 |

### 刻意不搬

| 來源 | 函式 | 保留位置 | 原因 |
|---|---|---|---|
| `views.py:553` | `_get_dialogue_session_record_for_user` | `api/views.py` | 直接建立 DRF `Response` 並指定 `status.HTTP_404_NOT_FOUND`，是 HTTP adapter，不是 session service。它只需改為 import 並呼叫 `dialogue_session._restore_dialogue_session_record_for_user`。把它原樣搬入 service 會讓 service layer 依賴 DRF。 |

沒有其他 helper 刻意留置，也沒有函式改名。

## 每個函式的 import 更新點

下列只列跨模組 import/lookup 更新；同一目標模組內的呼叫不新增 import。行號均指搬移前的 `views.py`。為保持 call-site diff 純機械化，`views.py` 對 service helper 使用直接 import（`from .services.<module> import ...`），不把呼叫改寫成 module-qualified 形式。

### `dialogue_session.py` 函式

| 函式 | import/lookup 更新點 |
|---|---|
| `_session_cache_key` | 無；只由同模組的 `_cache_dialogue_session_record`、`_restore_dialogue_session_record_for_user` 呼叫。`consumers.py:64` 是另一個本地同名函式，不是本函式的 importer，本 Part 不順手去重。 |
| `_cache_dialogue_session_record` | `api/views.py` import：`DialogueSessionCreateView.post` (`814`)、`DialogueSessionReplyView.post` (`975`)、`DialogueSessionSemanticTreeAnalyzeView.post` (`1053`)、`HistoryConversationSemanticTreeAnalyzeView.post` (`1236`)。同模組 restore 呼叫不新增 import。 |
| `_rebuild_session_state_from_turns` | 無；只由同模組 `_dialogue_session_cache_payload_from_record` 呼叫。 |
| `_dialogue_session_cache_payload_from_record` | `api/views.py` import：`HistoryConversationSemanticTreeTimelineView.get` (`1157`)、`HistoryConversationSemanticTreeAnalyzeView.post` (`1216`)；`api/services/history.py` import：`_history_ai_detail`（搬移前 `675`）。同模組 restore 呼叫不新增 import。 |
| `_persist_dialogue_session_record` | `api/views.py` import：`DialogueSessionCreateView.post` (`815`)、`DialogueSessionReplyView.post` (`976`)、`DialogueSessionSemanticTreeAnalyzeView.post` (`1054`)；`api/consumers.py:212` 改成 `from api.services.dialogue_session import _persist_dialogue_session_record`（呼叫在 `215`）。 |
| `_restore_dialogue_session_record_for_user` | `api/views.py` import：保留的 `_get_dialogue_session_record_for_user` (`554`)、`DialogueSessionLatestView.get` (`862`)、`DialogueSessionDetailView.get` (`883`)、`DialogueSessionReplyView.post` (`908`)；`api/consumers.py:190` 改成 `from api.services.dialogue_session import _restore_dialogue_session_record_for_user`（呼叫在 `193`）。 |
| `_dialogue_session_response_payload` | `api/views.py` import：`DialogueSessionLatestView.get` (`872`)、`DialogueSessionDetailView.get` (`893`)。 |
| `_update_ai_session_stance_drift` | `api/views.py` import：`DialogueSessionReplyView.post` (`970`)。 |
| `_build_topic_config` | `api/views.py` import：`DialogueSessionCreateView.post` (`777`)；同模組新增從 `api.services.stance_scoring` import `_compute_user_stance_score`、`_resolve_stances`、`_resolve_open_answers`。`backend/api/tests.py:506` 的 patch lookup 改為 `api.services.dialogue_session.build_q9_embedding`，因為 `build_q9_embedding` 會隨本函式的 module lookup 搬移。 |
| `_get_dialogue_runtime` | `api/views.py` import：`DialogueSessionCreateView.post` (`772`)、`DialogueSessionReplyView.post` (`904`)；同模組 `get_dialogue_agent` 呼叫不新增 import。 |
| `get_dialogue_agent` | `api/views.py` import：`DialogueSessionReplyView.post` (`946`)；`api/consumers.py:122` 改成 `from api.services.dialogue_session import get_dialogue_agent`（呼叫在 `149`）。`backend/api/tests_websocket.py:91` patch 改為 `api.services.dialogue_session.get_dialogue_agent`，因為 consumer 的 lookup 已改到 service。REST tests 在 `backend/api/tests.py:467,507,551,1775` patch 的 lookup 是 `api.views.get_dialogue_agent`；若 `views.py` 採直接 import，這四處應保留原 patch target。 |

### `stance_scoring.py` 函式

| 函式 | import/lookup 更新點 |
|---|---|
| `_get_survey_scoring_config` | 無；只由同模組 `_compute_user_stance_score`、`_resolve_stance_category`、`_resolve_open_answers` 呼叫。 |
| `_get_open_answer` | 無；只由同模組 `_resolve_open_answers` 呼叫。 |
| `_compute_user_stance_score` | `api/views.py` import：`MatchingJoinView.post` (`1295`)；`api/services/dialogue_session.py` import：`_build_topic_config`（搬移前 `357`）。 |
| `_resolve_stance_category` | `api/views.py` import：`DialogueSessionCreateView.post` (`822`)、`DialogueSessionReplyView.post` (`984`)、`MatchingJoinView.post` (`1299`)；`api/services/dialogue_session.py` import：`_dialogue_session_response_payload`（搬移前 `175`）；`backend/api/tests.py:23` 改成 `from api.services.stance_scoring import _resolve_stance_category`（呼叫在 `385,389,393,397`）。同模組 `_resolve_stances` 呼叫不新增 import。 |
| `_resolve_stances` | `api/services/dialogue_session.py` import：`_build_topic_config`（搬移前 `361`）。 |
| `_resolve_open_answers` | `api/views.py` import：`MatchingJoinView.post` (`1303`)；`api/services/dialogue_session.py` import：`_build_topic_config`（搬移前 `367`）。 |

### `history.py` 函式

| 函式 | import/lookup 更新點 |
|---|---|
| `_semantic_tree_root_name` | `api/views.py` import：`HistoryConversationSemanticTreeTimelineView.get` (`1177`)、`HistoryConversationSemanticTreeAnalyzeView.post` (`1258`)、三個 live matching semantic-tree views (`1470,1502,1543`)；同模組 history summary/detail 與 root-by-topic 呼叫不新增 import。 |
| `_semantic_tree_root_name_for_topic_id` | `api/views.py` import：兩個 dialogue semantic-tree views (`1011,1039`) 與兩個 history semantic-tree views (`1161,1222`)；同模組 `_semantic_tree_root_name`、`_history_ai_detail` 呼叫不新增 import。 |
| `_get_history_ai_record_for_user` | `api/views.py` import：`HistoryConversationDetailView.get` (`1101`)、`HistoryConversationSemanticTreeTimelineView.get` (`1147`)、`HistoryConversationSemanticTreeAnalyzeView.post` (`1206`)。 |
| `_history_ai_turns` | 無；只由同模組 `_history_ai_messages` 呼叫。 |
| `_history_ai_messages` | 無跨模組 import；只由同模組 `_history_ai_summary`、`_history_ai_detail` 呼叫。 |
| `_history_match_messages` | 無跨模組 import；只由同模組 `_history_match_summary`、`_history_match_detail` 呼叫。 |
| `_history_ai_summary` | `api/views.py` import：`HistoryConversationListView.get` (`1074`)；同模組 `_history_ai_detail` 呼叫不新增 import。 |
| `_history_match_summary` | `api/views.py` import：`HistoryConversationListView.get` (`1085`)；同模組 `_history_match_detail` 呼叫不新增 import。 |
| `_history_ai_detail` | `api/views.py` import：`HistoryConversationDetailView.get` (`1110`)、`HistoryConversationSemanticTreeAnalyzeView.post` (`1239`)；`history.py` 需從 `api.services.dialogue_session` import `_dialogue_session_cache_payload_from_record`。 |
| `_history_match_detail` | `api/views.py` import：`HistoryConversationDetailView.get` (`1122`)、`HistoryConversationSemanticTreeAnalyzeView.post` (`1274`)。 |

### `room_state.py` 函式

| 函式 | import/lookup 更新點 |
|---|---|
| `_get_other_user` | 無；只由同模組 `_build_matching_state_payload`、`_build_room_messages_payload` 呼叫。 |
| `_match_presence_fields` | 無；只由同模組 `_build_matching_state_payload`、`_build_room_messages_payload` 呼叫。 |
| `_build_matching_state_payload` | `api/views.py` import：`MatchingJoinView.post` (`1319`)、`MatchingStatusView.get` (`1339`)、`MatchingCancelView.post` (`1377`)、`MatchingRoomLeaveView.post` (`1575`)。 |
| `_room_match_state_status` | 無；只由同模組 `_build_room_messages_payload` 呼叫。 |
| `_get_latest_room_stance_drift` | 無；只由同模組 `_build_room_messages_payload` 呼叫。 |
| `_build_room_messages_payload` | `api/views.py` import：`MatchingRoomMessagesView.get/post` (`1404,1442`)。 |
| `_get_room_match_for_user` | `api/views.py` import：三個 history detail/timeline/analyze paths (`1113,1165,1245`)，`MatchingRoomMessagesView.get/post` (`1391,1415`)，三個 live semantic-tree views (`1457,1489,1523`)，以及 `MatchingRoomLeaveView.post` (`1565`)。 |
| `_touch_room_match_for_user_activity` | `api/views.py` import：`MatchingRoomMessagesView.get/post` (`1398,1421`) 與三個 live semantic-tree views (`1464,1496,1530`)。 |

### 保留在 `views.py` 的函式

| 函式 | import/lookup 更新點 |
|---|---|
| `_get_dialogue_session_record_for_user` | 不改定義位置；其內部 `_restore_dialogue_session_record_for_user` 改由 `api.services.dialogue_session` import。兩個現有 view callers (`1001,1027`) 不改 import。 |

### 模組 import 邊界與支援符號

機械搬移後的 service 依賴只允許下列單向關係：

- `views → dialogue_session, stance_scoring, history, room_state`
- `consumers → dialogue_session`
- `dialogue_session → stance_scoring`
- `history → dialogue_session, room_state`
- `stance_scoring` 與 `room_state` 不 import 其他 `api.services` 模組
- 四個 service 都不得 import `api.views`

為避免搬完函式後留下 `service → views` 反向依賴，以下 module-level 支援符號需隨擁有者更新 import；這些不是新功能或額外重構：

| 支援符號/lookup | 擁有者與更新點 |
|---|---|
| `SESSION_TTL_SECONDS`, `DEFAULT_DIALOGUE_COLLECTION` | 隨 session cache/persist/config 搬到 `dialogue_session.py`。 |
| `ANONYMOUS_MATCH_USER_NAME` | 搬到 `room_state.py`；`history.py` 從 `room_state.py` import。 |
| `logger` | `views.py` 保留 endpoint logger；`dialogue_session.py` 為 `_update_ai_session_stance_drift`、`_build_topic_config` 建立 module logger。 |
| `build_q9_embedding` | 從 `views.py` import block 移到 `dialogue_session.py`；對應測試 patch 更新見 `_build_topic_config`。 |
| `lru_cache`, `os`, `cache` | helper 搬完後由 `dialogue_session.py` import；若 `views.py` 已無其他使用則移除原 import。 |
| `timezone` | `dialogue_session.py` 為 persist helper 新增 import；`views.py` 的 history 排序仍使用，故原 import 保留。 |
| `TOPIC_CONFIGS` | `dialogue_session.py`（`_build_topic_config`）、`stance_scoring.py`（`_resolve_stances`）與 `history.py`（semantic root helpers）各自從 `api.dialogue_topics` import。 |
| `get_dialogue_survey` | `stance_scoring.py` import；`dialogue_session.py` 的 `_build_topic_config` 仍需 import，因其讀取 `semantic_vector_interface`；`views.py` 的 `DialogueSurveyView` 也保留原 import。 |
| `AIConversation`, `DialogueSessionRecord` | `dialogue_session.py` 與 `history.py` 各自從 `api.models` import；`views.py` 依 endpoint 使用情況保留。 |
| `DialogueMatch`, `MatchStanceDrift` | `room_state.py` 從 `api.models` import；`history.py` 另 import `DialogueMatch`。 |
| `Q` | `room_state.py` import；`views.py` 的 history query 仍使用，故原 import 也保留。 |
| `MatchingStateSerializer`, `MatchingRoomMessagesSerializer` | 移到 `room_state.py`；helper 搬完後從 `views.py` 移除。 |
| `MatchingRoomSemanticTreeSerializer` | `history.py` 新增 import；live endpoints 仍使用，因此 `views.py` 也保留。 |
| `Response`, `status` | 因 `_get_dialogue_session_record_for_user` 刻意留在 `views.py`，不得為此讓 `dialogue_session.py` import DRF。 |
| `api/services/__init__.py` | 目前 package 不存在；機械搬移時建立空的 `__init__.py` 即可，不需集中 re-export helper。 |

上述方向形成無循環依賴；若實際機械搬移發現額外隱藏循環，停止搬該函式、留在 `views.py`，並在 P4 handoff 記錄，不在搬移階段發明新抽象。
