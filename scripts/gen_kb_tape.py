"""產生首頁「觀點知識庫」卡片的膠帶拼貼書牆（frontend/public/kb_shelf_tape.png）。

參考照片是弧形書牆往右退縮的景深。這裡不畫真書：每層書架是一條木色膠帶，每本書
是一小段直立色膠帶，撕邊、微轉、半透明互相壓邊。書架列往右收斂到消失點、膠帶
同步縮小，透視感就出來了。

輸出 PNG 而不是 SVG：和紙的纖維、輕微斑駁、柔邊陰影都是像素層級的東西，用向量
形狀畫出來只會像色塊。鄰卡 .virtual-chat-entry 的背景也是 PNG，一致。
紙、陰影、雜訊、量化在 tape_common.py，跟遊戲地圖那張共用。

亂數固定種子：要能重跑重現，不然改一個參數整面書牆換位置，沒法比對。
    uv run scripts/gen_kb_tape.py      # 需要 pillow + numpy
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

from tape_common import finish, jitter, paper_layer

W, H = 720, 480
SS = 2                       # 超取樣倍率，縮回去就是抗鋸齒（撕邊要靠它才不會變階梯）
ROWS = 7
VANISH_Y = 0.52              # 書架列往右收斂到的高度（佔畫面比例）
DEPTH = 0.46                 # 右緣縮到左緣的（1 - DEPTH）
SEED = 20260829
OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "kb_shelf_tape.png"

# 書背用色：照片裡那些飽和書封，降彩度成和紙膠帶的調子
SPINES = [
    (209, 88, 79), (224, 138, 60), (224, 188, 85), (169, 185, 105),
    (111, 175, 156), (122, 165, 205), (155, 142, 196), (214, 142, 166),
    (239, 231, 216), (197, 111, 79), (79, 134, 127), (176, 74, 61),
    (201, 212, 168), (143, 184, 214), (232, 168, 107), (165, 101, 127),
]
BOARD = (192, 148, 98)


def scale(x):
    """透視縮放：越右邊越小。"""
    return 1 - DEPTH * (x / W)


def row_y(i, x):
    """第 i 列書架在 x 處的高度。左緣等距展開，往右收斂到消失點。"""
    y_left = H * 0.05 + i * (H * 0.995 / ROWS)
    return H * VANISH_Y + (y_left - H * VANISH_Y) * scale(x)


def torn_tape(x, y_bot, w, h, rot, rnd):
    """一段直立膠帶的多邊形：上緣撕痕折線，繞底部中心微轉。"""
    top = y_bot - h
    pts = [(x, y_bot), (x, top + rnd.uniform(0, 2.4))]
    for k in (0.28, 0.52, 0.76):
        pts.append((x + w * k, top + rnd.uniform(0, 2.8)))
    pts += [(x + w, top + rnd.uniform(0, 2.4)), (x + w, y_bot)]

    cx, cy, a = x + w / 2, y_bot, math.radians(rot)
    ca, sa = math.cos(a), math.sin(a)
    return [((px - cx) * ca - (py - cy) * sa + cx,
             (px - cx) * sa + (py - cy) * ca + cy) for px, py in pts]


def main():
    rnd = random.Random(SEED)
    paper = paper_layer(rnd, W * SS, H * SS, SS, SEED)
    tapes = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(tapes, "RGBA")
    gap = H * 0.995 / ROWS

    for i in range(ROWS):
        # 層板：沿著收斂線的四邊形，比書再厚一點
        th_l, th_r = 9.0, 9.0 * scale(W)
        board = [(0, row_y(i, 0)), (W, row_y(i, W)),
                 (W, row_y(i, W) + th_r), (0, row_y(i, 0) + th_l)]
        d.polygon([(px * SS, py * SS) for px, py in board], fill=BOARD + (238,))
        d.line([(0, (row_y(i, 0) + th_l - 1.5) * SS), (W * SS, (row_y(i, W) + th_r - 1) * SS)],
               fill=(120, 84, 48, 110), width=int(2.5 * SS))

        x = rnd.uniform(-8, 5)
        while x < W:
            s = scale(x)
            w = rnd.uniform(9, 21) * s
            h = gap * s * rnd.uniform(0.5, 0.86)
            poly = torn_tape(x, row_y(i, x) + 1.5, w, h, rnd.uniform(-3.5, 3.5), rnd)
            # 每段膠帶自己的色偏＋半透明：壓邊處會透出下面那段，才像和紙不像色塊
            c = jitter(rnd.choice(SPINES), rnd)
            d.polygon([(px * SS, py * SS) for px, py in poly],
                      fill=c + (rnd.randint(198, 226),))
            # 右緣一道暗邊：書背之間的接縫，也讓相鄰膠帶看得出前後
            d.line([(poly[-1][0] * SS, poly[-1][1] * SS), (poly[-2][0] * SS, poly[-2][1] * SS)],
                   fill=(0, 0, 0, 46), width=max(1, int(w * 0.16 * SS)))
            x += w - rnd.uniform(0.5, 2.4) * s      # 負間距＝膠帶互相壓邊
            if rnd.random() < 0.06:                 # 偶爾留個空位，別排太滿
                x += rnd.uniform(5, 16) * s

    # 幾何一路都是「往右收斂」比較好算，鏡射放在出圖前一步
    finish(paper, tapes, (W, H), SS, SEED, OUT, flip=True)


if __name__ == "__main__":
    main()
