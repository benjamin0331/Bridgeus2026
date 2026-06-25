extends CharacterBody2D


# Called when the node enters the scene tree for the first time.
var move_speed = 120
@onready var anim_sprite = $AnimatedSprite2D

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	
	# 取得移動向量與速度
	velocity = Input.get_vector("left","right","up","down")*move_speed
	
	# 判斷左右方向來翻轉圖片
	if velocity.x < 0:
		anim_sprite.flip_h = true 
	elif velocity.x > 0:
		anim_sprite.flip_h = false
	move_and_slide()
