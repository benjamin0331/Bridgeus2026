extends StaticBody2D
# 木樁座位。配對邏輯在 game.gd（server 權威），這裡只提供座位點與視覺回饋。

@onready var _sprite: Sprite2D = $Sprite2D
@onready var seat_point: Marker2D = $Marker2D

# 被玩家坐上/離開時由 game.gd 的 apply_seat/apply_unseat 本地呼叫（每個 peer 都會跑）。
func set_occupied(v: bool) -> void:
	# 發光=有人坐；還原=空位。
	_sprite.modulate = Color(1.5, 1.5, 1.5) if v else Color(1, 1, 1)
