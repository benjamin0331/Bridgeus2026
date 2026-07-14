extends CharacterBody2D

const SPEED = 150.0
@onready var camera = $Camera2D

# 👑 聽從 Godot 官方聖旨：在這裡設定權限，100% 解決 Spawner 報錯問題！
func _enter_tree():
	# 拿自己的名字（也就是剛剛傳進來的 str(id)）轉回整數，直接設為權限擁有者！
	var node_id = name.to_int()
	if node_id > 0:
		set_multiplayer_authority(node_id)

func _ready():
	# 🛡️ 權限檢查一：如果不是我這台電腦控制的，就把他的相機關掉
	if not is_multiplayer_authority():
		if camera:
			camera.enabled = false

func _physics_process(delta):
	# 🛡️ 權限檢查二：如果不是我這台電腦控制的，直接跳過不讀取鍵盤
	if not is_multiplayer_authority():
		return

	var direction = Input.get_vector("left", "right", "up", "down")
	if direction:
		velocity = direction * SPEED
	else:
		velocity = velocity.move_toward(Vector2.ZERO, SPEED)
		
	move_and_slide()
