class_name AnonNames
extends RefCounted
# 大廳的「表面匿名」顯示名稱。純資料 + 純函式，跟 Emoji.gd 一樣**不是 autoload**。
#
# POOL 的內容必須與後端 H-H 聊天室的匿名代號一字不差
# （../backend/apps/matching/services/anonymity.py 的 ANONYMOUS_IDS）——受試者
# 在遊戲大廳跟在網頁聊天室看到的是同一套稱呼，改一邊就要改另一邊。
#
# 刻意不從後端拉這份清單：大廳在本機開發時根本沒有後端可問（桌面 host 沒有
# 服務金鑰，見 CLAUDE.md「Spawn flow」），而這是顯示用常數、不是模組間契約，
# 拉一趟 HTTP 只會多一個開不了遊戲的理由。
#
# 指派方式跟後端不同、而且必須不同：後端是用 room_id 當種子抽樣（一間房兩個人，
# 純函式算得出來就好），大廳則是**人數不定、會進進出出**，只能由 server 看著
# 當下佔用中的名字逐一發號（見 game.gd 的 _peer_names）。

const POOL := [
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

# 名字還沒同步到（apply_names 尚未抵達、或該 peer 剛斷線）時的稱呼。
# 刻意不退回顯示 peer id——那正是這整個功能要消滅的東西。
const UNKNOWN := "某位玩家"


# 從池子裡挑一個 taken 裡沒有的名字。taken 是「當下已被佔用的名字」集合
# （Array 或 Dictionary 的 values()）。
#
# 池子只有 10 個而研究規模 ~60 人，大廳同時在線超過 10 位是可能的，所以第 11
# 位起加編號：香香泥 → 香香泥 2 → 香香泥 3。候選名字兩兩相異，所以在
# used.size() + 1 個候選之內一定挑得到空的，迴圈保證會終止。
static func pick(taken) -> String:
	var used := {}
	for t in taken:
		used[t] = true
	for i in range(used.size() + 1):
		var candidate := _nth(i)
		if not used.has(candidate):
			return candidate
	return UNKNOWN   # 不可能走到（見上方推論），留著是為了讓回傳型別完整。


# 第 i 個候選名字（i 從 0 開始）。整數除法取的是「繞了幾圈」。
static func _nth(i: int) -> String:
	var base: String = POOL[i % POOL.size()]
	var lap := i / POOL.size()
	return base if lap == 0 else "%s %d" % [base, lap + 1]
