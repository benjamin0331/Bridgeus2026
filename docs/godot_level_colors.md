# Godot 等級系統 — 七色青蛙

**畫面**：Godot 大廳（`godot/World/Game.tscn`）。玩家的青蛙顏色＝等級，右上角有豎排色表對照。
**等級來源**：累積完成對話場次，純衍生值，**後端不新增表也不新增欄位**。
**資料入口**：`GET /api/titles/me/` 的 `level` / `dialogue_count` / `level_thresholds`。

> 📌 本文件為等級功能的即時規格，等級門檻或青蛙配色一有更動就同步更新整份。

---

## 一、等級定義

**「完成」= 送出後測**，也就是 `PostDialogueResponse` 有一筆紀錄。這與成就頁「完成前測與後測」「完成完整聊天流程」是同一個定義，也是唯一每場對話都留下一筆的耐久紀錄。H-H 與 H-AI 都算（不看 `experiment_condition`）。

門檻（`backend/api/views.py::LEVEL_THRESHOLDS`）：

| 等級 | 顏色 | 門檻（累積完成場次）| 級距 | 涵蓋範圍 |
|---|---|---|---|---|
| Lv0 | 白 | 0 | — | 0–1 場 |
| Lv1 | 紅 | 2 | +2 | 2–4 |
| Lv2 | 橙 | 5 | +3 | 5–9 |
| Lv3 | 黃 | 10 | +5 | 10–16 |
| Lv4 | 綠 | 17 | +7 | 17–26 |
| Lv5 | 藍 | 27 | +10 | 27–39 |
| Lv6 | 紫 | 40 | +13 | 40+ |

級距刻意遞增，後段升級愈來愈慢。Lv0 門檻是 0，所以每個人一進來就有顏色（白青蛙）——`dialogue_level()` 仍保留「回傳預設 0」的防禦，是為了萬一哪天門檻表被改成 Lv0 > 0，新玩家也還是拿得到合法等級。門檻要調就改那一個 tuple，Godot 端不用動（色表的場次是從 `/titles/me/` 的 `level_thresholds` 讀的）。

等級不綁立場方向——刻意不用 `delta_s` / `stance_centrism` / drift 當來源。理由同 [settlement_screen.md](settlement_screen.md) §五.1：等級是比結算徽章更強的獎勵訊號，拿去極化程度當等級等於付錢請受試者改立場，實驗資料就廢了。

## 二、七色對照

配色由 [`scripts/recolor_frog.py`](../scripts/recolor_frog.py) **生成**，不是手繪。它對 `Assets/ToxicFrog/GreenBlue` 做 palette swap，只換身體 3 階，肚子／描邊保留，毒斑依身體明度自動選深／淺組。

| 等級 | 顏色 | 主色 | HSL | 中階 | 深階 |
|---|---|---|---|---|---|
| Lv0 | 白 | `#DFE8EC` | 200° 25% 90% | `#999EA6` | `#646571` |
| Lv1 | 紅 | `#DF2A3C` | 354° 74% 52% | `#992234` | `#641C2E` |
| Lv2 | 橙 | `#F27B2C` | 24° 88% 56% | `#A6572A` | `#6B3B28` |
| Lv3 | 黃 | `#F4D952` | 50° 88% 64% | `#A79442` | `#6C5F36` |
| Lv4 | 綠 | `#4DD039` | 112° 62% 52% | `#3A8E32` | `#2C5B2D` |
| Lv5 | 藍 | `#19C2F0` | 193° 88% 52% | `#1985A9` | `#185672` |
| Lv6 | 紫 | `#CA62DA` | 292° 62% 62% | `#8C479B` | `#5C326A` |

彩度基準是 char_2（BlueBrown）的身體色 `#2CE8F5`（S 90% / L 56%），原作六隻裡最高飽和的一隻，所以整組落在 S 62–88% / L 52–64%。

產圖：`uv run --with pillow python scripts/recolor_frog.py`（先跑 `--demo` 自我檢查）。輸出 `godot/Assets/ToxicFrog/Level/Frog_Lv{0..6}_{Idle,Hop}.png` 加一張貼在真實地面上的 `_contact_sheet.png`。

### 設計約束（改色前先讀）

- **靛被拿掉了**：藍/靛/紫擠在色環同一段，靛調亮撞藍、調暗撞紫，且在地圖上暗得像土。七色而非八色是為了拿回藍↔紫 94° 的色相間距。
- **橙不能移到別的明度**：紅綠色盲下綠↔橙必然撞（deutan ΔE 4.0）。試過把綠調暗閃開，變成撞紅（2.7，更糟）。彩虹順序的固有代價，靠色表的等級數字補。
- **不要貼合地圖的低彩度**：曾把彩度壓到 S 45–62% 想跟地圖（S 13–38% / L 28–46%）同調，結果橙和藍暗得像泥土。青蛙是前景角色，該比地面亮也該比地面豔。
- 常規色覺全 21 對都過（dataviz validator `--pairs all`，最差 ΔE 15.6 = 橙↔紅）。

## 三、資料流

```
PostDialogueResponse 筆數
  → views.py::dialogue_level()            後端算，不存欄位
  → GET /api/titles/me/  {level, dialogue_count, level_thresholds}
  → Backend.gd::get_my_titles()           快取進 Backend.level
  → player_00.gd::apply_level()           appearance = level
  → MultiplayerSynchronizer (spawn=true)  廣播給所有 peer，含晚進者
  → _update_anim()                        播 lv{appearance}_{idle|run}
```

等級掛在 `/titles/me/` 而不是新開端點：Godot 大廳本來就會打它拿頭銜，順路回傳零成本。**頭銜與等級語意分開**——頭銜是玩家自選的展示文字，等級是客觀資歷。

兩個時機都要顧：HTTP 回應和玩家按 Host/Join 的先後不固定，所以 `player_00.gd::_ready` 會讀 `Backend.level`，而 `game.gd::_apply_level_to_local_player` 在回應到達時再補設一次。

## 四、右上角等級色表

`game_ui.gd::_build_level_legend()` 在程式裡建（無 .tscn 改動）。豎排，每列＝一級：青蛙圖示 + `Lv{n} · {門檻}場`，玩家自己那一級加 `▸` 並提高不透明度。

**圖示用青蛙 sprite 本身，不是色塊** ——這樣顏色只有 `recolor_frog.py` 一個來源，UI 不會抄一份 hex 出來跟素材走鐘。等級數也是逐級探測 `Frog_LvN_Idle.png` 是否存在，跟 `player_00.gd::level_count()` 同一個原則：加一級只要加素材＋動畫。

## 五、邊界行為

| 情境 | 行為 |
|---|---|
| `guest_login`（桌面測試）| 每次建新 User → 一律 Lv0 白。不打 `/titles/me/` |
| 後端沒開 / 請求失敗 | `Backend.level` 維持 0，遊戲照跑；色表只顯示 Lv 編號、不顯示場次 |
| 對話中升級 | **不即時更新**，下次進場才變色。省掉整條推播路徑 |
| 升級提示 | Godot 內**不跳彈窗**。等級與場次常駐顯示在主功能成就頁最上面（見第七節） |
| 刷到稀有款 | 跳恭喜彈窗（每次刷到都跳）。這是 Godot 裡唯一的彈窗 |
| 後端等級數 > 素材數 | `apply_level()` 用 `clampi` 夾住，不會播不存在的動畫 |

## 五之二、Lv777 稀有款彩虹蛙（彩蛋，已啟用）

**每次進入 Godot 有 1/10 機率**刷到，取代該場的等級色青蛙。不持久化——下次進場重新擲，沒刷到就正常顯示等級色。

- 素材：`Frog_Lv777_{Idle,Hop}.png`，由 `recolor_frog.py --rainbow` 產生。色相由像素座標決定（斜向掃過整隻，含肚子），每格推進 1/幀數 → 播放時彩虹會流動，一輪動畫剛好一個循環。描邊與眼睛不彩虹化，否則整隻失去輪廓。
- 觸發：`player_00.gd::roll_appearance()`，在 `_ready()`（authority）擲一次。混入 peer id 當亂源，因為多開實例同時啟動時時間種子會撞在一起。
- **777 是哨兵值，不是第 777 級**：`_update_anim` 用 `"lv%d_%s"` 組動畫名，所以 `appearance = 777` 就自然播到 `lv777_idle`，零對映程式碼，而且照樣走 MultiplayerSynchronizer → 別人也看得到你刷到稀有款。
- `apply_level()` 在 `is_rare()` 時不套等級色（否則 `/titles/me/` 回來會把彩虹蛙覆寫掉），但色表照刷。
- `level_count()` 是從 0 依序探測到缺號為止，所以 777 不會被算成一個等級。
- **色表不標箭頭**：刷到稀有款時 `refresh_level_legend(false)`，整欄都沒有 `▸`、也不高亮任何一列，因為玩家的青蛙不屬於任何一級。

## 六、主功能成就頁的等級摘要

`frontend/src/pages/LevelSummaryCard.jsx`，掛在 `AchievementPage` 最上面。三塊：**Godot 等級**（`Lv{n}` + 顏色名）、**已完成對話**（場次）、**離 Lv{n+1}**（還差幾場，最高級時不顯示），左邊一隻大青蛙。

「Godot 等級」旁邊有一個小問號，hover／鍵盤 focus 時顯示「等級隨完成的對話場次而提升，可以在 Godot 中解鎖不同形象的青蛙」。用 `<button>` 而非 `<span>`，這樣鍵盤操作也叫得出提示（`title` 屬性只吃滑鼠）。

**層次感靠三件事**：卡片背景是真的 Godot 地圖；青蛙 154×119，比地圖裡的 32px 圖磚大得多；而且 `top: -40px` 讓牠**上緣 1/3 露出卡片外**，再加 `drop-shadow`。牠是站在地圖前面的角色，不是貼在地圖上的圖示。

青蛙因此是卡片左側的獨立 absolute 元素，不再是「Lv2」前面的行內圖 —— 行內的話上方有標籤文字擋著，放不大也溢不出去。卡片 `padding-left: 204px` 讓位，`margin-top: 56px` 留給溢出的 40px 加呼吸空間。

背景上的米色薄紗是**左濃右淡的橫向漸層**（0.92 → 0.42），不是整片同一個濃度。文字全部集中在左側到中段，那裡需要 0.9 才讀得清——均勻 0.55 時字壓在紅樹上就看不見了；右側沒有文字，留 0.42 讓地圖看得出來。這樣「可讀性」與「看得到遊戲世界」不必二選一。標籤與數值另外加了白色 `text-shadow` 當保險，標籤色也從頁面通用的 `#8d7058` 加深成 `#6d543c`。

資料來自 `GET /api/titles/me/`，跟 Godot 同一支端點。**抓不到就退回寫死的示意資料**並在卡上顯示「示意資料 · 後端尚未串接」的紅色標籤，console 也會留一行警告。

早期版本是「抓不到就整張不顯示」（`return null`），那是錯的：後端沒回應時整張卡直接消失，看的人不知道是功能沒做好還是資料沒到，也無從除錯。串接狀態要在畫面上看得出來。串好之後把 `PLACEHOLDER` / `isPlaceholder` / `.level-summary-placeholder` 一起刪（見 [0804.md](0804.md) §2.1）。

## 六之二、網頁用的圖從哪來

| 檔案 | 怎麼來的 | 用在哪 |
|---|---|---|
| `lv0.png`~`lv6.png`（66×51） | `scripts/export_web_assets.py`：各等級 idle 第一格，裁掉空白後 3 倍 | 等級卡的青蛙 |
| `frog_idle_green.png`（528×51） | 同上：綠蛙 idle 8 格接成一條橫圖 | 首頁虛擬大廳右下角的待機動畫 |
| `map_bg.png`（880×441） | **人工從遊戲場景截圖**，不是產生的 | 等級卡 **與** 虛擬大廳卡片的背景 |

**改了 `recolor_frog.py` 的配色之後要重跑 `export_web_assets.py`**，不然網頁上的青蛙會跟遊戲裡的不一樣。這也是為什麼卡片用真的 sprite 圖而不是 CSS 色點——色碼不再需要在前端複製一份。

### 背景為什麼用截圖而不是產生的

曾經寫了 `scripts/render_map_region.py`，直接解析 `Game.tscn` 的 `TileMapLayer`（base64 `PackedByteArray`，每格 12 bytes）指定世界座標重畫地圖。技術上跑得通、也還原了大部分地形，但**水域被畫成大片深褐色方塊**——動畫水磚與多格圖磚的取樣沒對，看起來像破洞，完全不能用。那支腳本已移除，避免它覆蓋現在這張截圖。

要換背景就換掉 `frontend/public/frogs/map_bg.png` 這個檔，CSS 兩處都吃同一張。目前 `background-position: center 35%`，讓岸邊到草地的過渡落在等級卡的可視範圍內。

### 響應式

等級卡把青蛙尺寸做成唯一的旋鈕：

```css
--frog-w: clamp(88px, 11vw, 154px);
--frog-h: calc(var(--frog-w) * 17 / 22);   /* sprite 是 22:17 */
--frog-out: calc(var(--frog-h) / 3);       /* 上緣露出卡片外的高度 */
```

卡片的 `padding-left`、`margin-top`、`min-height`、青蛙的 `top` 全部由這三個算出來，所以視窗縮放時整張卡等比變化。早期版本把 154×119 寫死，畫面縮小時青蛙不縮、把版面撐爆。

`max-width: 640px` 時青蛙回到正常流排在最左邊（`position: static`，72px），不再溢出也不吃左內距——手機上硬留 88px 的左側空位會把三塊數字擠成一行一個字。

首頁大廳角落的動畫**不是 GIF**：8 格橫條 + CSS `steps(8)` 逐格跳 `background-position`，另加 `scaleX(-1)` 讓牠面向卡片內側。透明度乾淨、像素不會被壓、不用多一個檔案格式。實作在 `HomePage.css` 的 `.virtual-chat-entry::after`，用 pseudo-element 而非 React 元素，因為它純裝飾（螢幕閱讀器該跳過），而且 `ActionCard` 不必為了一個裝飾多開 `children`。

## 六之三、首頁兩張入口卡的散落裝飾

`frontend/src/components/ScatterDecor.jsx`（＋同名 CSS）。鋪滿卡片的絕對定位裝飾層，`pointer-events: none` 所以卡片本身的 `onClick` 照常運作。

| 卡片 | 裝飾 |
|---|---|
| 虛擬大廳 | 6 隻等級青蛙（`set="frogs"`），大小 24–34px、角度 ±20° 隨機感、半透明 |
| 觀點知識庫 | 5 個遊戲內表情圖示（`set="emoji"`）—— 知識庫就是各種觀點的沉澱 |

**綠色的 lv4 不在散落清單裡**：牠是右下角那隻會動的，重複出現會撞在一起。

**座標是手挑的固定值，不用 `Math.random()`**：亂數會讓每次 re-render 都跳位，熱重載時圖案一直變，看起來像 bug。固定值一樣是不規則的視覺效果，但穩定。窄畫面對整層做 `scale(0.72)`（不是逐張改寬度），位置會一起往中間收，剛好避開變窄後更靠近的標題。

`ActionCard` 為此多了一個 `children` prop —— 文字仍走 `text`，既有呼叫端不用改。虛擬大廳的標題另外加了白色 `text-shadow`，因為它會壓在地圖的樹幹上。

## 七、待辦

- [ ] `char_0`～`char_5` 舊動畫仍留在 SpriteFrames 但永不播放（保留以便退回隨機外觀）。確定不退回後可清掉，連同 `Assets/ToxicFrog/` 底下六個原始配色資料夾。
- [ ] 色盲玩家分辨不出紅/橙/綠三階，目前只靠色表的等級數字。若要更保險，可在青蛙頭上直接顯示等級數字。
