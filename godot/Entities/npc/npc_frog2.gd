extends "res://Entities/npc/npc_frog.gd"
# 第二隻青蛙：靠近提示、對話開場等行為全部沿用 npc_frog.gd，換掉台詞——而且中間
# 那幾句不是寫死的，是即時從後端撈的「公共政策網路參與平臺（join.gov.tw）目前的
# 熱門提案」，給不知道要貼什麼議題的玩家當引子。
#
# 資料鏈（要換內容是動這條鏈，不是改這個檔）：
#   爬蟲 JSON（backend/data/join_ideas/）
#     → manage.py import_join_ideas   （每個 section 整批換掉）
#     → PolicyIdea 資料表
#     → GET /api/policy-ideas/?section=hot
#     → Backend.get_policy_ideas()
#     → 這裡

# 一次講幾則。上限不是技術限制是耐心：對話框一行一則，多了就變成一直按空白鍵。
const HOT_COUNT := 3

const LINES_HEAD: Array[String] = [
	"呱，不知道該分享什麽議題的話這邊跟你分享目前公共政策網路參與平臺的熱門討論議題呱",
]
const LINES_TAIL: Array[String] = [
	"當然除了這些你有自己想分享的議題都能提出來呱",
	"但要記得禮貌呱，促進友好的討論環境不要讓自己功德-1-1呱",
	"祝你玩的開心，呱",
]
# 撈不到時（桌面開發沒登入、後端沒開、請求逾時）用這句頂著。青蛙其餘的台詞都不
# 依賴後端，為了一段撈不到的內容讓整段教學都講不出來並不划算。
const FALLBACK_LINE := "唔，我這邊一時翻不到最新的提案清單呱，你先自己想想有什麼想聊的吧呱"

var _hot_lines: Array[String] = []
# 0 = 還沒去要、1 = 在途中、2 = 已結束（成功或失敗都算結束）
var _fetch_state := 0
signal _hot_lines_settled

func _on_body_entered(body: Node) -> void:
	super(body)
	# 走近就先去要。玩家還得按一次「也請與我對話呱」才會開對話框，那段時間通常
	# 夠一趟 HTTP 跑完，開場就不必為了等網路而卡著。
	# 只為本地玩家發：遠端玩家經過這隻青蛙不該害每個 peer 各打一次 API。
	if body.is_in_group("players") and body.is_multiplayer_authority():
		_ensure_hot_lines()

# 覆寫 npc_frog.gd 的開場：資料還在路上就等它回來再開口。不先用備援台詞開場是因為
# 對話框沒有「把已經顯示出去的那一句換掉」的介面，先講了就補不回來。
func _start_talk(dm) -> void:
	_ensure_hot_lines()
	if _fetch_state == 1:
		await _hot_lines_settled
	dm.start_dialogue(_lines())

func _ensure_hot_lines() -> void:
	if _fetch_state != 0:
		return   # 一場只要一次：後端存的本來就是快照，不會在一場遊戲裡變
	if Backend.access_token == "":
		# 桌面開發／直接開 export：沒有身份就沒有這支 API，不必送出去換一個 401。
		_fetch_state = 2
		return
	_fetch_state = 1
	Backend.get_policy_ideas("hot", HOT_COUNT, func(code, data):
		if code == 200 and data is Array:
			_hot_lines = _to_lines(data)
		else:
			# 出聲但不擋路：玩家會拿到備援台詞，而排查的人需要知道是哪一步斷的。
			push_warning("教學青蛙取熱門提案失敗 code=%d" % code)
		_fetch_state = 2
		_hot_lines_settled.emit()
	)

# 一則提案一句台詞。附議數要帶上：那是「這件事有多少人在意」最直觀的數字，
# 也是玩家判斷要不要拿它當議題的依據。
func _to_lines(rows: Array) -> Array[String]:
	var out: Array[String] = []
	for row in rows:
		if typeof(row) != TYPE_DICTIONARY:
			continue
		var title := str(row.get("title", "")).strip_edges()
		if title == "":
			continue
		out.append("「%s」——目前有 %d 人附議呱" % [title, int(row.get("endorse_count", 0))])
	return out

func _lines() -> Array[String]:
	var out: Array[String] = []
	out.append_array(LINES_HEAD)
	if _hot_lines.is_empty():
		out.append(FALLBACK_LINE)
	else:
		out.append_array(_hot_lines)
	out.append_array(LINES_TAIL)
	return out
