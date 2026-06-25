extends StaticBody2D

@onready var sprite = $Sprite2D
@onready var area = $Area2D
@onready var seat_point = $Marker2D

var player_node = null
var is_sitting = false
var original_pos = Vector2.ZERO

func _ready():
	# 1. 綁定雷達 (Area2D) 偵測實體進出的訊號
	area.body_entered.connect(_on_body_entered)
	area.body_exited.connect(_on_body_exited)

func _process(_delta):
	# 2. 如果玩家在範圍內，且還沒坐下，偵測 F 鍵
	if player_node and not is_sitting:
		if Input.is_physical_key_pressed(KEY_F):
			sit_down()
			
	# 3. 如果已經坐下，偵測 S 鍵退回原點
	elif is_sitting:
		if Input.is_physical_key_pressed(KEY_S):
			stand_up()

# 當有物件走進 Area2D 圓圈時觸發
func _on_body_entered(body):
	# 檢查進來的是不是玩家 (根據你主地圖的節點名稱)
	if body.name == "player_00" or body.name == "player_01":
		player_node = body
		# 💡 視覺回饋：讓樹幹變亮 (數值大於 1 就是發光)
		sprite.modulate = Color(1.5, 1.5, 1.5)

# 當物件離開 Area2D 圓圈時觸發
func _on_body_exited(body):
	if body == player_node:
		# 💡 修復 Nil 報錯：如果玩家正在坐著，絕對不可以清空 player_node！
		if is_sitting:
			return 
			
		player_node = null
		sprite.modulate = Color(1, 1, 1)

# 執行坐下邏輯
func sit_down():
	is_sitting = true
	original_pos = player_node.global_position
	player_node.global_position = seat_point.global_position
	player_node.set_physics_process(false)
	
	# 💡 修復圖層被遮擋：強制把玩家的顯示圖層 (z_index) 拉到最前面！
	player_node.z_index = 1

# 執行站起邏輯
func stand_up():
	is_sitting = false
	player_node.global_position = original_pos
	player_node.set_physics_process(true)
	
	# 💡 恢復圖層：把玩家的圖層放回預設的 0
	player_node.z_index = 0
	
	# 確保站起來後，如果玩家已經不在雷達圈內，要手動關閉發光狀態
	if not area.overlaps_body(player_node):
		player_node = null
		sprite.modulate = Color(1, 1, 1)
