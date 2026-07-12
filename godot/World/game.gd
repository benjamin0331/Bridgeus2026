extends Node2D

var peer = WebSocketMultiplayerPeer.new()
const PORT = 8080
const ADDRESS = "127.0.0.1" 

@onready var host_btn = $CanvasLayer/UI_Root/HostButton
@onready var join_btn = $CanvasLayer/UI_Root/JoinButton

func _ready():
	host_btn.pressed.connect(_on_host_pressed)
	join_btn.pressed.connect(_on_join_pressed)

# 1. Host 點擊方法
func _on_host_pressed() -> void:
	var error = peer.create_server(PORT)
	if error != OK:
		print("無法啟動 WebSocket 伺服器，錯誤碼：", error)
		return
		
	multiplayer.multiplayer_peer = peer
	print("內建 WebSocket 伺服器已啟動！")
	_spawn_player(1) # 老大（Host）給自己生身體
	hide_buttons()

# 2. Join 點擊方法
func _on_join_pressed() -> void:
	var error = peer.create_client("ws://" + ADDRESS + ":" + str(PORT))
	if error != OK:
		print("無法連接 WebSocket，錯誤碼：", error)
		return
		
	multiplayer.multiplayer_peer = peer
	print("正在嘗試連線到本機...")
	hide_buttons()
	
	# 📣 【核心關鍵】一連上線，立刻向 Server 廣播：發放我的網路 ID 並幫我生身體！
	# 這裡我們用一個延遲，確保網路 peer 已經完全握手成功
	await get_tree().create_timer(0.2).timeout
	var my_id = multiplayer.get_unique_id()
	request_spawn.rpc_id(1, my_id)

# 3. 遠端申請角色生成的傳送門（只有 Server 1 號會執行它）
@rpc("any_peer", "call_local")
func request_spawn(id):
	if multiplayer.is_server():
		print("Server 收到申請！準備幫玩家生成網路 ID: ", id)
		_spawn_player(id)

# 4. 唯一的生成角色方法（由 Server 執行，Spawner 會自動空投給所有人）
func _spawn_player(id):
	var player_scene = preload("res://Entities/player/player_00.tscn")
	var player = player_scene.instantiate()
	
	# 1. 唯一的任務：把名字改成網路 ID
	player.name = str(id) 
	
	# 2. 直接放進世界，剩下的權限交給小人自己處理！
	add_child(player)

func hide_buttons():
	host_btn.hide()
	join_btn.hide()
