"""把 ToxicFrog GreenBlue 的 3 階身體色換成等級色，產出 Lv0–Lv7 八套 sprite。

素材是 Endesga 32 色盤的 index color，換色是精確的像素對映而非濾鏡：只動身體
3 階，肚子/描邊/毒斑原樣保留（八隻才會像同一物種的不同階段）。

陰影兩階不是硬填色，而是主色朝描邊色 #181425 混合 —— 這個做法能還原原素材
（t=0.35/0.62 算出來 ≈ #3E8948 / #265C42），且對任何色相都成立。早期版本改用
「沿用綠色素材的色相位移」，套到暖色上會把陰影推成亮綠，不要回頭走那條路。

ponytail: 只處理 tscn 實際用到的 Idle / Hop；其餘動作（Attack/Hurt/Explosion）
沒進場景就不產，要用再加進 ACTIONS。
"""
import colorsys
import sys
from pathlib import Path
from PIL import Image

SRC = Path("godot/Assets/ToxicFrog/GreenBlue")
OUT = Path("godot/Assets/ToxicFrog/Level")
ACTIONS = ("Idle", "Hop")

BODY = ["#63C74D", "#3E8948", "#265C42"]   # 原素材身體 3 階（亮→暗）
# 毒斑 2 階：原素材是藍的。八階不能共用同一組斑色 —— 身體從 L=.94 的白橫跨到 L=.22 的
# 靛，固定淺斑會在黃/白身上糊掉（黃身配淺灰斑明度太近，看起來就是髒），固定深斑在靛/紅
# 身上又會消失。所以改成兩組中性斑色，每階挑跟身體明度差最大的那組（見 pick_spots）。
SPOT_SETS = (
    {"#0099DB": "#C0CBDC", "#124E89": "#8B9BB4"},   # 淺斑：給深色身體
    {"#0099DB": "#3A4466", "#124E89": "#262B44"},   # 深斑：給亮色身體
)
OUTLINE = "#181425"                        # 陰影混合的錨點，也是可讀性的下限
SHADE_T = (0.0, 0.35, 0.62)                # 各階朝 OUTLINE 的混合比例

# Lv0–Lv7 身體主色（OKLab 明度單調遞減；配色理由見 docs 等級配色一節）
# Lv0–Lv6 身體主色：白 + 紅橙黃綠藍紫（**七級**，不是八級）。
#
# 靛被拿掉了：藍/靛/紫三個擠在色環同一段，靛不管調亮調暗都跟旁邊兩個拉不開，而且它是
# 三個裡最暗的、在地圖上看起來像土。少一色換來藍↔紫 94° 的色相間距，整組才分得開。
#
# 彩度參考 char_2（BlueBrown）的身體色 #2CE8F5 —— S 90% / L 56%，是原作六隻裡最高飽和
# 的一隻。所以這組一律 S 62–88% / L 52–64%。前一版壓到 S 45–62% 想貼合地圖的低彩度土色
# 系，結果橙和藍暗得像泥土；青蛙是前景角色，該比地面亮也該比地面豔，不需要跟地面同調。
#
# 常規色覺全 21 對都過（最差 ΔE 15.6 = 橙↔紅）。紅綠色盲下綠↔橙仍會撞（deutan ΔE 4.0），
# 彩虹順序無解，靠等級數字當第二編碼。
LEVELS = ["#DFE8EC",   # Lv0 白  H200 S25 L90
          "#DF2A3C",   # Lv1 紅  H354 S74 L52
          "#F27B2C",   # Lv2 橙  H24  S88 L56
          "#F4D952",   # Lv3 黃  H50  S88 L64
          "#4DD039",   # Lv4 綠  H112 S62 L52
          "#19C2F0",   # Lv5 藍  H193 S88 L52（貼近 char_2 的 #2CE8F5）
          "#CA62DA"]   # Lv6 紫  H292 S62 L62

# 陰影不能貼上描邊亮度，否則整隻糊成剪影。地板分階給：最深階只需要贏過描邊，用同一個
# 較嚴的地板會把深色身體（靛 Lv6）的兩階夾成同色、失去立體感。demo() 會擋住這種退化。
MARGIN = {1: 0.09, 2: 0.05}


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lum(rgb):
    r, g, b = [c / 255 for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def shades(main_hex):
    """主色 → 3 階。混合比例會在撞到 OUTLINE 亮度時自動收斂，保住可讀性。"""
    main, anchor = hexrgb(main_hex), hexrgb(OUTLINE)
    out = [main]
    for step, t in enumerate(SHADE_T[1:], start=1):
        floor = lum(anchor) + MARGIN[step]
        while t > 0 and lum(lerp(main, anchor, t)) < floor:
            t -= 0.02
        out.append(lerp(main, anchor, t))
    return out


def pick_spots(main_hex):
    """挑跟身體明度差最大的那組斑色，保證每階的斑點都看得見。"""
    l = lum(hexrgb(main_hex))
    return max(SPOT_SETS, key=lambda d: abs(l - lum(hexrgb(next(iter(d.values()))))))


# --- Lv777 彩虹蛙（彩蛋）--------------------------------------------------
# 一般等級是「一個主色 → 導出 3 階」，彩虹蛙反過來：色相由像素座標決定（斜向掃過整隻），
# 明度沿用原素材的分層，所以還是看得出立體感而不是一團色噪。每格把色相往前推 1/幀數
# → 播動畫時彩虹會流動，一輪動畫剛好推完一個完整循環，接回第一格不會跳。
#
# 兩個調過才對的地方：
#   RAINBOW_BAND 一開始給 34，青蛙身上塞了一輪多，看起來像彩紙屑而不是彩虹。拉到 64
#   （約兩倍身寬）才變成乾淨的一道漸層掃過去。
#   肚子原本保留原色，結果整隻中間一大塊土黃，彩虹效果被切斷。現在肚子也吃彩虹，
#   只是用更亮的階（RAINBOW_TIERS 後三個），看起來像在發光。
RAINBOW_BAND = 64.0
# 彩度刻意壓在 0.42。調整過程：0.95（第一版）在遊戲裡是螢光級、0.62 仍然刺眼，
# 0.34 以下就洗成粉彩、看不出是「炫彩」。判斷要在**遊戲比例**下做（相機 2.5x、
# 貼在真實地圖上），縮圖上看起來剛好的值放到遊戲裡通常還是太亮。
# 明度階同時各提一點補回被彩度帶走的分量，不然降彩度會讓整隻變灰。
RAINBOW_S = 0.42
# 原素材色 → 彩虹的明度階。身體 3 階 + 肚子 3 階；描邊與眼睛（#181425）刻意不碰，
# 不然整隻會失去輪廓、在亮色地面上看不出形狀。
RAINBOW_TIERS = {
    "#63C74D": 0.62, "#3E8948": 0.46, "#265C42": 0.32,   # 身體 亮/中/暗
    "#EAD4AA": 0.86, "#E4A672": 0.78, "#B86F50": 0.66,   # 肚子 亮/中/暗
}
RAINBOW_SPOT = {"#0099DB": "#FFFFFF", "#124E89": "#D8DEE9"}   # 毒斑：純白／冷白當亮點


def rgb_from_hls(h, l, s):
    return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))


def rainbow_frame(frame, phase):
    """把一格的彩色像素換成彩虹。phase 0–1，逐格遞增造成流動感。"""
    tiers = {hexrgb(k): v for k, v in RAINBOW_TIERS.items()}
    spot = {hexrgb(k): hexrgb(v) for k, v in RAINBOW_SPOT.items()}
    px = frame.load()
    w, h = frame.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            key = (r, g, b)
            if key in spot:
                px[x, y] = (*spot[key], a)
            elif key in tiers:
                hue = ((x + y) / RAINBOW_BAND + phase) % 1.0
                px[x, y] = (*rgb_from_hls(hue, tiers[key], RAINBOW_S), a)
    return frame


def make_rainbow():
    OUT.mkdir(exist_ok=True)
    for action in ACTIONS:
        src = Image.open(SRC / f"ToxicFrogGreenBlue_{action}.png").convert("RGBA")
        n = src.width // 48
        out = Image.new("RGBA", src.size)
        for f in range(n):
            out.paste(rainbow_frame(src.crop((f * 48, 0, f * 48 + 48, 48)), f / n), (f * 48, 0))
        out.save(OUT / f"Frog_Lv777_{action}.png")
    print("wrote Frog_Lv777_Idle.png / Frog_Lv777_Hop.png（彩蛋，未接進等級系統）")


def recolor(img, mapping):
    px = list(img.getdata())
    img.putdata([(*mapping[p[:3]], p[3]) if p[3] and p[:3] in mapping else p for p in px])
    return img


def main():
    OUT.mkdir(exist_ok=True)
    sheets = []
    for lv, main_hex in enumerate(LEVELS):
        mapping = dict(zip((hexrgb(c) for c in BODY), shades(main_hex)))
        mapping.update({hexrgb(k): hexrgb(v) for k, v in pick_spots(main_hex).items()})
        for action in ACTIONS:
            img = recolor(Image.open(SRC / f"ToxicFrogGreenBlue_{action}.png").convert("RGBA"), mapping)
            img.save(OUT / f"Frog_Lv{lv}_{action}.png")
            if action == "Idle":
                sheets.append(img.crop((0, 0, 48, 48)))
        print(f"Lv{lv} {main_hex} -> " + " ".join("#%02X%02X%02X" % c for c in shades(main_hex)))

    # 對照圖：八隻並排貼在真實地面上放大 4 倍。背景是必要的 —— 這組色是照地圖的
    # 低彩度土色系調的，在白底上判斷不出協調性，只有貼在草地/土地上才看得出來。
    tiles = Image.open("godot/Assets/ForgottenMemories/TileSet.png").convert("RGBA")
    grass, dirt = tiles.crop((832, 320, 880, 368)), tiles.crop((1280, 320, 1328, 368))
    sheet = Image.new("RGBA", (48 * len(LEVELS), 96))
    for i, im in enumerate(sheets):
        for row, bg in enumerate((grass, dirt)):
            sheet.paste(bg, (48 * i, 48 * row))
            sheet.alpha_composite(im, (48 * i, 48 * row))
    sheet.resize((48 * len(LEVELS) * 4, 96 * 4), Image.NEAREST).save(OUT / "_contact_sheet.png")
    print(f"wrote {len(LEVELS) * len(ACTIONS)} sprites + _contact_sheet.png")


def demo():
    """自我檢查：3 階必須明度嚴格遞減、都比描邊亮，且 8 級主色不重複。"""
    floor = lum(hexrgb(OUTLINE))
    for main_hex in LEVELS:
        ls = [lum(c) for c in shades(main_hex)]
        assert ls[0] > ls[1] > ls[2] > floor, (main_hex, ls, floor)
    assert len(set(LEVELS)) == len(LEVELS)   # 沒有重複色（等級數改動時也成立）
    # 原素材要能被自己的公式還原（誤差容許 index color 的量化）
    got = shades(BODY[0])
    for want, g in zip(BODY[1:], got[1:]):
        assert max(abs(a - b) for a, b in zip(hexrgb(want), g)) < 20, (want, g)
    # 斑色跟身體的明度差要夠，否則斑點會糊進身體（舊版黃色就是這樣壞掉的）
    for main_hex in LEVELS:
        spot = next(iter(pick_spots(main_hex).values()))
        d = abs(lum(hexrgb(main_hex)) - lum(hexrgb(spot)))
        assert d > 0.15, f"{main_hex} 的斑色明度差只有 {d:.3f}，太糊"
    print("demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    elif "--rainbow" in sys.argv:
        make_rainbow()
    else:
        main()
