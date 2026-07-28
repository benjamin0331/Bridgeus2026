class_name Emoji
extends RefCounted
# 表情回復用的 5 個圖示（純資料表，用 class_name 全域取用，不必當 autoload）。
# 素材是 128x128、8x8 的 16px 網格。UI 按鈕和玩家頭上表情列共用這份，避免座標散落兩處。
# index 對應 REGIONS 順序；改順序/增減都只動這裡。

const SHEET := preload("res://Assets/16x16_emoji_asset_pack_v1.1.png")
# 使用者挑的 5 格：第1排#1、第3排#8、第2排#6、第2排#1、第7排#4。
const REGIONS := [
	Rect2(0, 0, 16, 16),
	Rect2(112, 32, 16, 16),
	Rect2(80, 16, 16, 16),
	Rect2(0, 16, 16, 16),
	Rect2(48, 96, 16, 16),
]

static var _cache: Array = []

static func tex(i: int) -> AtlasTexture:
	if _cache.is_empty():
		for r in REGIONS:
			var t := AtlasTexture.new()
			t.atlas = SHEET
			t.region = r
			_cache.append(t)
	return _cache[i] if i >= 0 and i < _cache.size() else null
