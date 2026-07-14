extends CharacterBody2D


# Called when the node enters the scene tree for the first time.
var move_speed = 120

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	velocity = Input.get_vector("left","right","up","down")*move_speed
	move_and_slide()
