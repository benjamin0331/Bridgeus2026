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
var _invite_is_voice := false
var _voice_peer_id := -1
var _react_buttons: Array[Button] = []
var _react_bars: Array[ColorRect] = []
var _reacted_targets := {}   # target_id → 已選的 idx（一人對一議題只回一個表情）
var _count_col: VBoxContainer
var _count_labels: Array[Label] = []
const REACT_COL_W := 120.0   # Read 面板右側表情統計欄寬度

@onready var _menu: Panel = $Menu
@onready var _menu_title: Label = $Menu/MenuTitle
@onready var _read: Panel = $Read
@onready var _read_title: Label = $Read/ReadTitle
@onready var _read_body: RichTextLabel = $Read/ReadBody
@onready var _read_close: Button = $Read/CloseButton
@onready var _invite: Panel = $Invite
@onready var _invite_label: Label = $Invite/InviteLabel
@onready var _chat: Panel = $Chat
@onready var _chat_log: RichTextLabel = $Chat/ChatLog
@onready var _chat_input: LineEdit = $Chat/ChatInput
@onready var _chat_send_btn: Button = $Chat/SendButton
@onready var _toast: Label = $Toast
@onready var _voice: Panel = $Voice
@onready var _voice_title: Label = $Voice/VoiceTitle
@onready var _pitch_slider: HSlider = $Voice/PitchSlider
@onready var _pitch_label: Label = $Voice/PitchLabel
@onready var _bars: Array[ColorRect] = [$Voice/Bar1, $Voice/Bar2, $Voice/Bar3, $Voice/Bar4, $Voice/Bar5]

# 聲波柱：頻段、換算旋鈕（實機微調靈敏度）。
const _FREQ_BINS := [[20, 200], [200, 500], [500, 1500], [1500, 3000], [3000, 10000]]
const _HEIGHT_SCALE := 400.0
const _MIN_BAR := 5.0
const _MAX_BAR := 50.0
const _BAR_BASELINE := 205.0   # 柱子底部 y（Voice 面板內），往上長

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
	$Menu/VoiceButton.pressed.connect(_send_voice_invite)
	$Voice/QuitButton.pressed.connect(_quit_voice)
	_pitch_slider.value_changed.connect(_on_pitch)
	_build_reaction_buttons()
	_build_reaction_counts()
	_build_level_legend()
	_build_rare_popup()

# --- 等級色表（畫面右上角）------------------------------------------------
# 讓玩家看得懂「哪個顏色是哪一級」。圖示直接用青蛙 sprite 本身，不是色塊 ——
# 這樣顏色只有 scripts/recolor_frog.py 一個來源，UI 不會抄一份 hex 出來跟素材走鐘。
const LEGEND_SPRITE := "res://Assets/ToxicFrog/Level/Frog_Lv%d_Idle.png"
const LEGEND_MAX := 16          # 上限保護：檔案是逐級探測的，避免哪天出錯就無限迴圈
# 青蛙在 48×48 的格子裡只佔這一塊，其餘是透明空白（量測值：七個等級 × 全部 idle 幀的
# 聯集，見 git 記錄的 bbox 量測）。直接用整格當圖示的話一半以上是空的，看起來就特別小。
# 素材若重畫成別的尺寸，這個 region 要重量。
const LEGEND_ICON_REGION := Rect2(11, 16, 22, 17)

# 整個色表的縮放倍率——調這一個數字就等比放大／縮小（圖示、字、間距、內距全跟著走）。
# 4/3 = 比基準大 1/3。基準（1.0）是 102×160，這裡是約 136×213。
#
# ⚠️ 非整數倍會讓像素畫的格子大小不均（有些原始像素佔 1px、有些佔 2px），青蛙輪廓
# 會微微歪。1.0 / 2.0 這種整數倍才是 1:1 乾淨的。目前 4/3 是刻意換取尺寸剛好。
const LEGEND_ZOOM := 4.0 / 3.0

const LEGEND_FONT_PX := 11      # 以下都是 ZOOM = 1.0 時的基準值
const LEGEND_MARGIN := 8.0
const LEGEND_PAD := 6.0         # 面板內距
const LEGEND_ROW_SEP := 4.0     # 圖示與文字的水平間距
const LEGEND_COL_SEP := 2.0     # 列與列的垂直間距

var _legend_labels: Array[Label] = []   # index = 等級，用來標出「你在這一級」

func _build_level_legend() -> void:
	var panel := PanelContainer.new()
	panel.name = "LevelLegend"
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE   # 純顯示，不吃點擊
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.15, 0.15, 0.15, 0.85)        # 同議題泡泡/頭銜底色
	sb.set_corner_radius_all(4)
	sb.set_content_margin_all(LEGEND_PAD * LEGEND_ZOOM)
	panel.add_theme_stylebox_override("panel", sb)

	var font_px := roundi(LEGEND_FONT_PX * LEGEND_ZOOM)
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", roundi(LEGEND_COL_SEP * LEGEND_ZOOM))
	var head := Label.new()
	head.text = "等級"
	head.add_theme_font_size_override("font_size", font_px)
	col.add_child(head)

	for lv in LEGEND_MAX:
		var path: String = LEGEND_SPRITE % lv
		if not ResourceLoader.exists(path):
			break        # 等級數以素材為準，跟 player_00.gd::level_count() 同一個原則
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", roundi(LEGEND_ROW_SEP * LEGEND_ZOOM))
		# 只取第一格裡青蛙本體那一塊。若整格 48×48 都取，會連帶一整條 8 格；
		# 若取整格但不裁白邊，青蛙只佔一半、看起來很小。
		var atlas := AtlasTexture.new()
		atlas.atlas = load(path)
		atlas.region = LEGEND_ICON_REGION
		var icon := TextureRect.new()
		icon.texture = atlas
		icon.custom_minimum_size = LEGEND_ICON_REGION.size * LEGEND_ZOOM
		icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST   # 像素畫不要模糊
		row.add_child(icon)
		var label := Label.new()
		label.add_theme_font_size_override("font_size", font_px)
		label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		row.add_child(label)
		_legend_labels.append(label)
		col.add_child(row)

	panel.add_child(col)
	add_child(panel)
	# 右上角。寬高都刻意設成 0（offset_left = offset_right、offset_bottom = offset_top）
	# ——Control 會把自己的尺寸夾到 get_combined_minimum_size()，所以面板剛好貼合內容，
	# 不會像給固定寬度那樣在文字右邊留一大片空灰底。grow 方向決定它往哪邊長：往左、往下。
	# 別改成在這裡呼叫 get_combined_minimum_size()：add_child 當下 layout 還沒跑過，
	# 那時候會拿到 0，面板就變成零尺寸、整個色表看不見。
	panel.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	panel.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	panel.grow_vertical = Control.GROW_DIRECTION_END
	var margin := LEGEND_MARGIN * LEGEND_ZOOM
	panel.offset_right = -margin
	panel.offset_left = -margin
	panel.offset_top = margin
	panel.offset_bottom = margin
	refresh_level_legend(true)   # 玩家還沒生成，先按「正常顯示等級」畫；擲完稀有款會再刷

# 文字內容依後端資料變動（門檻、目前等級），所以跟建構分開；player_00.gd 在擲完稀有款
# 之後、game.gd 在 /titles/me/ 回來後都會呼叫。後端沒開時只顯示 Lv 編號，不顯示場次。
#
# show_marker=false 用在玩家刷到稀有款彩虹蛙時：那隻不屬於任何一級，整欄都不該有箭頭，
# 也不該把某一列高亮成「你在這」。下次進場沒刷到就會再傳 true 回來。
func refresh_level_legend(show_marker: bool) -> void:
	for lv in _legend_labels.size():
		var text: String = "Lv%d" % lv
		if lv < Backend.level_thresholds.size():
			text += " · %d場" % int(Backend.level_thresholds[lv])
		var here := show_marker and lv == Backend.level
		_legend_labels[lv].text = ("▸ " + text) if here else text
		_legend_labels[lv].modulate = Color.WHITE if here else Color(1, 1, 1, 0.55)

# --- 稀有款彈窗 ------------------------------------------------------------
# 只有一種內容：刷到稀有款彩虹蛙的恭喜訊息。
#
# 曾經還有一個「首次解鎖某一級」的進度彈窗，已移除 —— 等級與已完成場次改成常駐顯示在
# 主功能的成就頁最上面（frontend/src/pages/AchievementPage.jsx），玩家隨時看得到，
# 不需要用彈窗打斷遊戲。順帶也免掉了「彈窗跳過了沒」那個 user:// 已讀狀態，
# 那份本機紀錄換瀏覽器就會失效，本來是要請後端補欄位的（見 docs/0804.md）。
const POPUP_W := 360.0
const POPUP_FONT_PX := 15

var _popup: PanelContainer
var _popup_label: Label

func _build_rare_popup() -> void:
	_popup = PanelContainer.new()
	_popup.name = "RarePopup"
	_popup.visible = false
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.11, 0.11, 0.13, 0.96)
	sb.border_color = Color(1, 1, 1, 0.25)
	sb.set_border_width_all(1)
	sb.set_corner_radius_all(6)
	sb.set_content_margin_all(16)
	_popup.add_theme_stylebox_override("panel", sb)

	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 12)
	_popup_label = Label.new()
	_popup_label.add_theme_font_size_override("font_size", POPUP_FONT_PX)
	_popup_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_popup_label.custom_minimum_size = Vector2(POPUP_W, 0)
	_popup_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	col.add_child(_popup_label)
	var ok := Button.new()
	ok.text = "確定"
	ok.custom_minimum_size = Vector2(96, 32)
	ok.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	ok.pressed.connect(func(): _popup.visible = false)
	col.add_child(ok)
	_popup.add_child(col)
	add_child(_popup)
	# 置中：寬高都設 0 讓 Control 夾到內容最小值，再靠 grow 往兩側對稱長開。
	_popup.set_anchors_preset(Control.PRESET_CENTER)
	_popup.grow_horizontal = Control.GROW_DIRECTION_BOTH
	_popup.grow_vertical = Control.GROW_DIRECTION_BOTH

func _show_popup(text: String) -> void:
	_popup_label.text = text
	_popup.visible = true

# 刷到稀有款彩虹蛙 —— 每次刷到都跳（就是要讓玩家知道自己中了）。
func show_rare_popup() -> void:
	_show_popup("恭喜你在本次探索中，獲得了稀有形態的炫彩青蛙！")

# Read 面板底部一排表情按鈕：按下 → 對正在讀的對方議題送出表情回復。
# 一人對一議題只有一個表情，但可改選：目前選的那個底部顯示灰條，按別的就換過去。
func _build_reaction_buttons():
	for i in Emoji.REGIONS.size():
		var b := Button.new()
		b.icon = Emoji.tex(i)
		b.expand_icon = true   # 16px 圖示放大填滿按鈕
		b.offset_left = 24 + i * 56
		b.offset_top = 172
		b.offset_right = b.offset_left + 48
		b.offset_bottom = 172 + 44
		b.pressed.connect(_on_react.bind(i))
		_read.add_child(b)
		_react_buttons.append(b)
		# 底部灰條：標記「我對這個對象選的表情」。滑鼠穿透，不擋按鈕點擊。
		var bar := ColorRect.new()
		bar.color = Color(0.5, 0.5, 0.5)
		bar.offset_left = 0
		bar.offset_right = 48
		bar.offset_top = 40
		bar.offset_bottom = 44
		bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
		bar.visible = false
		b.add_child(bar)
		_react_bars.append(bar)

# Read 面板右側竪排：5 個表情各自獲得幾個（emoji ×N），讓旁人一眼看出獲得了哪些。
func _build_reaction_counts():
	_count_col = VBoxContainer.new()
	_count_col.add_theme_constant_override("separation", 4)
	for i in Emoji.REGIONS.size():
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 6)
		var tr := TextureRect.new()
		tr.texture = Emoji.tex(i)
		tr.custom_minimum_size = Vector2(24, 24)
		tr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		row.add_child(tr)
		var lbl := Label.new()
		lbl.add_theme_font_size_override("font_size", 18)
		lbl.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		lbl.text = "×0"
		row.add_child(lbl)
		_count_col.add_child(row)
		_count_labels.append(lbl)
	_read.add_child(_count_col)

func _on_react(idx: int):
	if _target == null:
		return
	var tid = _target.name.to_int()
	if _reacted_targets.get(tid, -1) == idx:
		return   # 已是這個表情，點同一個沒作用
	var p = _local()
	if p:
		p.react_to_issue(_target, idx)   # 可改選：作者端覆蓋，頭上同步換圖示
		_reacted_targets[tid] = idx
		_sync_reaction_buttons()   # 灰條從舊選的移到新選的
		# 頭上顯示走上面的 P2P；這裡另外把表情存回後端當研究資料（後端 upsert）。
		# 需要對方議題的後端 id（作者存過後端才有）＋自己已登入。作者的 backend
		# POST 還沒回來（id 仍為 0）時就先只跑 P2P、不落地，屬可接受的邊界。
		if Backend.access_token != "" and _target.backend_issue_id > 0:
			Backend.add_issue_reaction(_target.backend_issue_id, idx)

# 開啟面板時，依「我是否已回過這個對象」還原灰條狀態。
func _sync_reaction_buttons():
	var chosen = _reacted_targets.get(_target.name.to_int(), -1) if _target else -1
	for i in _react_bars.size():
		_react_bars[i].visible = i == chosen

# 統計 _target.reactions 裡每個 idx 的數量，更新右側竪排。
func _refresh_reaction_counts():
	if _target == null:
		return
	var counts := {}
	for idx in _target.reactions:
		counts[idx] = counts.get(idx, 0) + 1
	for i in _count_labels.size():
		_count_labels[i].text = "×%d" % counts.get(i, 0)

# 玩家節點收到新表情時呼叫：若正開著這個人的議題就即時更新數量。
func on_reactions_changed(player):
	if _read.visible and _target == player:
		_refresh_reaction_counts()

# 某人重新提交/刪除議題（表情歸零）時呼叫：清掉本地對他的「已回過」鎖，面板開著就刷新。
func on_issue_reset(player):
	_reacted_targets.erase(player.name.to_int())
	if _read.visible and _target == player:
		_sync_reaction_buttons()
		_refresh_reaction_counts()

# 聲波柱：通話中每幀抓頻譜能量更新 5 根柱子高度（由底往上長）。
func _process(_delta):
	if not _voice.visible:
		return
	for i in _bars.size():
		var raw = VoiceChat.get_spectrum(_FREQ_BINS[i][0], _FREQ_BINS[i][1])
		var target = clampf(_MIN_BAR + raw * _HEIGHT_SCALE, _MIN_BAR, _MAX_BAR)
		var cur = _bars[i].offset_bottom - _bars[i].offset_top
		var h = lerpf(cur, target, 0.3)
		_bars[i].offset_top = _BAR_BASELINE - h
		_bars[i].offset_bottom = _BAR_BASELINE

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
	if _target == null or suppress_menu or _read.visible or _invite.visible or _voice.visible:
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
	_sync_reaction_buttons()    # 還原「我對這人選過的表情」灰條
	_refresh_reaction_counts()  # 右側數量統計
	_layout_read()   # 聊天開著時右邊縮短，不與聊天視窗相撞
	_read.visible = true
	_update_menu()

# 聊天視窗從 offset_left=840 起，議題面板開著就把右緣縮到 820 留間距；否則用全寬 1140。
func _layout_read():
	var right := 820.0 if _chat.visible else 1140.0
	_read.offset_right = right
	var inner := right - _read.offset_left   # 面板內部可用寬度
	# 右側保留 REACT_COL_W 給表情統計欄，標題/內文右緣再往左讓開。
	_read_title.offset_right = inner - REACT_COL_W - 24
	_read_body.offset_right = inner - REACT_COL_W - 24
	_read_close.offset_left = inner - 120
	_read_close.offset_right = inner
	if _count_col:
		_count_col.offset_left = inner - REACT_COL_W
		_count_col.offset_top = 16
		_count_col.offset_right = inner - 8
		_count_col.offset_bottom = 152   # 5 行 ~136px，落在關閉鈕(y172)之上不相撞

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
func show_invite(from_id: int, is_voice := false):
	_invite_from = from_id
	_invite_is_voice = is_voice
	var kind = "語音通話" if is_voice else "聊天"
	_invite_label.text = "玩家 %d 想和你%s" % [from_id, kind]
	_invite.visible = true
	_update_menu()

func _answer_invite(accepted: bool):
	_invite.visible = false
	_update_menu()
	var p = _local()
	if p:
		if _invite_is_voice:
			p.respond_voice_invite(_invite_from, accepted)
		else:
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

# --- voice call (open_voice / voice_peer_left called by the player) --------
func _send_voice_invite():
	if _target == null:
		return
	var p = _local()
	if p:
		p.invite_to_voice(_target)
		notify("已送出語音邀請")

func open_voice(other_id: int):
	_voice_peer_id = other_id
	_voice_title.text = "語音通話中（玩家 %d）" % other_id
	# 滑桿歸中性（原音）+ 更新說明文字。
	_pitch_slider.value = 1.0
	_pitch_label.text = "音高 低沉↔尖細：1.00"
	_voice.visible = true
	_update_menu()   # 隱藏靠近選單，避免和語音面板重疊

# 滑桿即時調音高 + 更新說明文字。
func _on_pitch(v: float):
	VoiceChat.set_pitch(v)
	_pitch_label.text = "音高 低沉↔尖細：%.2f" % v

func _quit_voice():
	var p = _local()
	if p and _voice_peer_id >= 0:
		p.leave_voice(_voice_peer_id)
	_voice.visible = false
	_voice_peer_id = -1
	_update_menu()   # 面板關了，靠近選單恢復顯示

func voice_peer_left(from_id: int):
	if not _voice.visible or from_id != _voice_peer_id:
		return
	notify("對方已退出語音")
	_voice.visible = false
	_voice_peer_id = -1
	_update_menu()

# --------------------------------------------------------------------------
func notify(msg: String):
	_toast.text = msg
	_toast.visible = true
	await get_tree().create_timer(2.0).timeout
	if _toast.text == msg:
		_toast.visible = false
