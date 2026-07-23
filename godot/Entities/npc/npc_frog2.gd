extends "res://Entities/npc/npc_frog.gd"
# 第二隻青蛙：靠近提示、對話開場等行為全部沿用 npc_frog.gd，只換台詞。
# 之後把 LINES 換成你要説的話即可。

const LINES: Array[String] = [
	"呱，不知道該分享什麽議題的話這邊跟你分享目前公共政策網路參與平臺的熱門討論議題呱", 
	"[這邊放後端資料庫的東西Claude記得改這裏]", 
	"當然除了這些你有自己想分享的議題都能提出來呱",
	"但要記得禮貌呱，促進友好的討論環境不要讓自己功德-1-1呱",
	"祝你玩的開心，呱",
	]

func _lines() -> Array[String]:
	return LINES
