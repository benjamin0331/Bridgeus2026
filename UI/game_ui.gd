extends CanvasLayer
# Reactive interaction UI: proximity menu / read panel / invite prompt / chat / toast.
# Local & screen-space only — ZERO RPCs (those live on the player). The player finds
# this node via the "issue_ui" group and calls the public methods below.
# The submit-issue form lives elsewhere (game.gd + IssueForm), not here.

var _target: Node = null       # player currently in range (for the menu)
var _chat_peer_id := -1
var _invite_from := -1
var _chat_active := false
var suppress_menu := false     # game.gd raises this while the submit form is open

@onready var _menu: Panel = $Menu
@onready var _menu_title: Label = $Menu/MenuTitle
@onready var _read: Panel = $Read
@onready var _read_title: Label = $Read/ReadTitle
@onready var _read_body: Label = $Read/ReadBody
@onready var _read_close: Button = $Read/CloseButton
@onready var _invite: Panel = $Invite
@onready var _invite_label: Label = $Invite/InviteLabel
@onready var _chat: Panel = $Chat
@onready var _chat_log: RichTextLabel = $Chat/ChatLog
@onready var _chat_input: LineEdit = $Chat/ChatInput
@onready var _chat_send_btn: Button = $Chat/SendButton
@onready var _toast: Label = $Toast

func _ready():
	add_to_group("issue_ui")
	$Menu/ReadButton.pressed.connect(_open_read)
	$Menu/InviteButton.pressed.connect(_send_invite)
	$Read/CloseButton.pressed.connect(_close_read)
	$Invite/AcceptButton.pressed.connect(func(): _answer_invite(true))
	$Invite/RejectButton.pressed.connect(func(): _answer_invite(false))
	$Chat/CloseButton.pressed.connect(_close_chat)
	_chat_input.text_submitted.connect(func(_t): _send_chat())
	_chat_send_btn.pressed.connect(_send_chat)

func _local():
	for p in get_tree().get_nodes_in_group("players"):
		if p.is_multiplayer_authority():
			return p
	return null

# --- proximity menu (called by the player) --------------------------------
func show_interaction(target):
	_target = target
	_update_menu()

# Re-read the current target's issue (called when a nearby player submits).
func refresh_menu():
	_update_menu()

func _update_menu():
	# Hide the menu while a modal (form/read/invite) is open so they don't overlap.
	if _target == null or suppress_menu or _read.visible or _invite.visible:
		_menu.visible = false
		return
	var t = _target.issue_title if _target.issue_title != "" else "（對方尚未提交議題）"
	_menu_title.text = "議題：" + t
	_menu.visible = true

# 讀議題面板開啟時鎖住移動，關掉才能動。
func blocks_movement() -> bool:
	return _read.visible

func _open_read():
	if _target == null:
		return
	if _target.issue_title == "":
		notify("對方還沒有提交議題")
		return
	_read_title.text = _target.issue_title
	_read_body.text = _target.issue_body if _target.issue_body != "" else "（沒有補充內容）"
	_layout_read()   # 聊天開著時右邊縮短，不與聊天視窗相撞
	_read.visible = true
	_update_menu()

# 聊天視窗從 offset_left=840 起，議題面板開著就把右緣縮到 820 留間距；否則用全寬 1140。
func _layout_read():
	var right := 820.0 if _chat.visible else 1140.0
	_read.offset_right = right
	var inner := right - _read.offset_left   # 面板內部可用寬度
	_read_title.offset_right = inner - 24
	_read_body.offset_right = inner - 24
	_read_close.offset_left = inner - 120
	_read_close.offset_right = inner

func _close_read():
	_read.visible = false
	_update_menu()

func _send_invite():
	if _target == null:
		return
	var p = _local()
	if p:
		p.invite_to_chat(_target)
		notify("已送出聊天邀請")

# --- invite prompt (called by the player on the invited side) -------------
func show_invite(from_id: int):
	_invite_from = from_id
	_invite_label.text = "玩家 %d 想和你聊天" % from_id
	_invite.visible = true
	_update_menu()

func _answer_invite(accepted: bool):
	_invite.visible = false
	_update_menu()
	var p = _local()
	if p:
		p.respond_invite(_invite_from, accepted)

# --- chat (open_chat / append_chat called by the player) ------------------
func open_chat(other_id: int):
	_chat_peer_id = other_id
	_chat.visible = true
	_layout_read()   # 議題面板若已開著，縮短以讓出聊天視窗空間
	_set_chat_active(true)
	_chat_input.grab_focus()

func _close_chat():
	# Tell the other side before tearing down, but only if the chat is still live
	# (don't echo back to a peer who already left).
	if _chat_active and _chat_peer_id >= 0:
		var p = _local()
		if p:
			p.leave_chat(_chat_peer_id)
	_chat.visible = false
	_chat_peer_id = -1
	_set_chat_active(false)
	_layout_read()   # 聊天關了，議題面板恢復全寬

func peer_left_chat(from_id: int):
	if not _chat.visible or from_id != _chat_peer_id:
		return
	_chat_log.append_text("[i]對方已退出聊天[/i]\n")
	_set_chat_active(false)

func _set_chat_active(active: bool):
	_chat_active = active
	_chat_input.editable = active
	_chat_send_btn.disabled = not active

func _send_chat():
	if not _chat_active:
		return
	var text = _chat_input.text.strip_edges()
	if text == "" or _chat_peer_id < 0:
		return
	var p = _local()
	if p:
		p.send_chat(_chat_peer_id, text)
	append_chat(multiplayer.get_unique_id(), text)
	_chat_input.text = ""

func append_chat(from_id: int, text: String):
	# Auto-open the window if a message arrives before it's shown.
	if not _chat.visible:
		open_chat(from_id)
	var who = "我" if from_id == multiplayer.get_unique_id() else "玩家 %d" % from_id
	_chat_log.append_text("[b]%s：[/b]%s\n" % [who, text])

# --------------------------------------------------------------------------
func notify(msg: String):
	_toast.text = msg
	_toast.visible = true
	await get_tree().create_timer(2.0).timeout
	if _toast.text == msg:
		_toast.visible = false
