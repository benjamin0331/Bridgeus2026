"""把 Godot 的青蛙 sprite 與地圖磚匯出成網頁能直接用的圖，放進 frontend/public/frogs/。

前端沒辦法讀 res:// 的資源，所以等級卡上的青蛙、虛擬大廳角落的動畫青蛙、以及卡片的
地圖背景都要先在這裡產好。改了 recolor_frog.py 的配色之後要重跑這支。

    uv run --with pillow python scripts/export_web_assets.py

產出：
    lv0.png ~ lv6.png        等級卡上的青蛙頭像（單格，裁掉空白，3 倍）
    lv777.png                稀有款炫彩青蛙，同規格。虛擬大廳的散落裝飾會用到
    emoji0.png ~ emoji4.png  觀點知識庫卡片上散落的表情裝飾。座標刻意跟 Globals/Emoji.gd
                             的 REGIONS 一致，所以網頁上看到的就是玩家在遊戲裡按的那 5 個

卡片背景 map_bg.png 不在這裡也不是產生的 —— 那是人工從遊戲場景截的圖。曾經寫過腳本
解析 Game.tscn 重畫地圖，但水域會變成大片深褐色方塊，所以放棄了（見
docs/godot_level_colors.md §六之二）。要換背景直接換那個 PNG 檔。
"""
from pathlib import Path
from PIL import Image

LEVEL_DIR = Path("godot/Assets/ToxicFrog/Level")
OUT = Path("frontend/public/frogs")

# 青蛙在 48×48 格子裡的實際範圍（量測值，同 game_ui.gd 的 LEGEND_ICON_REGION）。
# 不裁的話一半以上是透明空白，網頁上排版會被那圈空白撐開。
FROG_BOX = (11, 16, 33, 33)
SCALE = 3                 # 整數倍，NEAREST → 不會有縮放毛邊
EMOJI_SHEET = Path("godot/Assets/16x16_emoji_asset_pack_v1.1.png")
# 與 godot/Globals/Emoji.gd::REGIONS 同一組座標（128×128、8×8 的 16px 網格）。
# 那邊改了這裡要跟著改 —— 目的就是讓網頁裝飾與遊戲內的表情是同一批圖。
EMOJI_REGIONS = [(0, 0), (112, 32), (80, 16), (0, 16), (48, 96)]
EMOJI_PX = 16
EMOJI_SCALE = 3

def frog_frame(level: int, index: int) -> Image.Image:
    sheet = Image.open(LEVEL_DIR / f"Frog_Lv{level}_Idle.png").convert("RGBA")
    x0, y0, x1, y1 = FROG_BOX
    return sheet.crop((index * 48 + x0, y0, index * 48 + x1, y1))


def upscale(img: Image.Image) -> Image.Image:
    return img.resize((img.width * SCALE, img.height * SCALE), Image.NEAREST)


def export_heads() -> int:
    n = 0
    while (LEVEL_DIR / f"Frog_Lv{n}_Idle.png").exists():
        upscale(frog_frame(n, 0)).save(OUT / f"lv{n}.png")
        n += 1
    # 稀有款是哨兵編號 777，不在連號裡，所以單獨處理（同 player_00.gd::level_count 的道理）
    if (LEVEL_DIR / "Frog_Lv777_Idle.png").exists():
        upscale(frog_frame(777, 0)).save(OUT / "lv777.png")
    return n


def export_emoji() -> int:
    sheet = Image.open(EMOJI_SHEET).convert("RGBA")
    for i, (x, y) in enumerate(EMOJI_REGIONS):
        cell = sheet.crop((x, y, x + EMOJI_PX, y + EMOJI_PX))
        cell.resize((EMOJI_PX * EMOJI_SCALE, EMOJI_PX * EMOJI_SCALE), Image.NEAREST)             .save(OUT / f"emoji{i}.png")
    return len(EMOJI_REGIONS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    levels = export_heads()
    emoji = export_emoji()
    head_w = (FROG_BOX[2] - FROG_BOX[0]) * SCALE
    head_h = (FROG_BOX[3] - FROG_BOX[1]) * SCALE
    print(f"lv0–lv{levels - 1}.png     {head_w}x{head_h}（{levels} 張）")
    print(f"emoji0–emoji{emoji - 1}.png  {EMOJI_PX * EMOJI_SCALE}x{EMOJI_PX * EMOJI_SCALE}（{emoji} 張）")
    print("map_bg.png 不在這裡產 —— 那是人工截的遊戲畫面，要換就直接換檔")


if __name__ == "__main__":
    main()
