"""產生遊戲地圖的膠帶拼貼（frontend/public/frogs/map_tape.png）。

首頁「虛擬大廳」卡片與成就頁等級卡共用這張圖，取代原本那張 Godot 實拍截圖
map_bg.png（保留在原地當這支腳本的參考底稿，不要刪）。

畫的是同一個場景、不是同一種畫法：像素圖靠一格一格的色塊，膠帶拼貼靠一條一條半透明
的和紙互相壓邊。所以這裡只保留場景的骨架 —— 池塘的 L 形水域、砂堤、石岸、左邊的
柵欄木樁、鏽紅闊葉樹、深綠針葉樹、青綠小樹、右側裸土 —— 細節（磚紋、花草、玩家角色）
全部丟掉：貼在卡片上只會糊成雜訊，而且上面還壓著一層薄紗。

兩件事決定了它輕不輕透：

1. 疊色用乘算（tape_common.multiply），不是貼不透明色塊。和紙是透光的，看到的顏色是
   「底下的紙 × 膠帶的透光率」，所以下面的色表全都調得比成品淺 —— 那是透光率不是顏色。
   壓邊處自動變深，這就是膠帶感的來源。大面積因此都用「一條一條互相壓邊」鋪，接縫本身
   就是紋理（水面的橫向波紋是這樣長出來的，不是另外畫的）。
2. 會被壓到的東西先把形狀挖回白紙再貼（group()）。水如果直接乘在草地上會變成髒墨綠，
   針葉樹乘在水上會變成黑；實體上你本來就不會在水下面先鋪一層草。

    uv run scripts/gen_map_tape.py      # 需要 pillow + numpy
"""

import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from tape_common import blob, finish_flat, jitter, multiply, paper_layer, torn

W, H = 880, 441
SS = 2
SEED = 20260830
OUT = (Path(__file__).resolve().parent.parent
       / "frontend" / "public" / "frogs" / "map_tape.png")

# 整體濃度的旋鈕。卡片上還壓著一層薄紗，所以這裡看起來剛好的濃度，貼上去會再淡一階；
# 這個值是對著「跟隔壁書牆那張卡等重」調的，改色表之後通常要回頭重調它。
TAPE = 1.18

# ⚠️ 以下是「透光率」不是成品顏色：乘算之後會比這裡看到的更深。
GRASS = (182, 194, 140)
GRASS2 = (170, 185, 125)
WATER = (146, 206, 224)
WATER2 = (127, 191, 214)
WATER3 = (162, 215, 229)
SAND = (236, 226, 190)
ROCK = (168, 170, 164)
SHORE = (194, 173, 137)
DIRT = (215, 189, 153)
DIRT2 = (202, 174, 138)
BARK = (178, 144, 114)
RUST = [(233, 168, 126), (214, 120, 84), (190, 95, 64)]
PINE = [(124, 170, 128), (103, 152, 111), (144, 188, 144)]
TEAL = [(152, 218, 222), (133, 203, 211)]
BUSH = (166, 194, 138)


def main():
    rnd = random.Random(SEED)
    paper = np.asarray(paper_layer(rnd, W * SS, H * SS, SS, SEED), np.float32)
    cv = paper.copy()

    def px(pts):
        """正規化座標 → 超取樣後的像素座標。場景用 0~1 描述，改構圖時才讀得懂。"""
        return [(x * W * SS, y * H * SS) for x, y in pts]

    def shape(pts, amp=0.004, seg=0.04):
        return px(torn(pts, amp, rnd, seg=seg))

    def lay(pts, color, alpha, edge=0, cj=8):
        multiply(cv, pts, jitter(color, rnd, cj), min(255, round(alpha * TAPE)), edge)

    def band(y, x0, x1, h, color, alpha, edge=0, skew=0.0):
        """一條橫向膠帶。skew 讓左右兩端不等高，貼起來才不像印上去的。"""
        return shape([(x0, y + skew), (x1, y), (x1, y + h), (x0, y + h + skew)])

    def bl(cx, cy, rx, ry, n=15, wob=0.16):
        return shape(blob(cx, cy, rx, ry, rnd, n=n, wob=wob), amp=0.006, seg=0.06)

    def group(pieces):
        """先把整組的外形挖回白紙，再一片片乘上去 —— 這組東西不該吃到底下的顏色。

        只用在「顏色會被底下毀掉」的東西：水域、裸土、樹冠。樹幹、草叢、柵欄一律直接
        乘在草地上 —— 半透明的膠帶貼在白紙上會比貼在草地上淺一大截，該連著的東西挖成
        白底反而會鑲一圈淺邊，看起來像挖破了。"""
        m = Image.new("L", (W * SS, H * SS), 0)
        md = ImageDraw.Draw(m)
        for pts, *_ in pieces:
            md.polygon(pts, fill=255)
        mm = np.asarray(m, np.float32)[:, :, None] / 255.0
        cv[:] = cv * (1 - mm) + paper * mm
        for pts, color, alpha, edge in pieces:
            lay(pts, color, alpha, edge)

    # --- 草地：八條橫向寬膠帶互相壓邊鋪滿，接縫就是草地的層次 ---
    for i in range(6):
        lay(band(-0.10 + i * 0.195 + rnd.uniform(-.02, .02), -0.03, 1.03,
                 0.26 + rnd.uniform(-.03, .04), None, None, skew=rnd.uniform(-0.02, 0.02)),
            rnd.choice((GRASS, GRASS2)), rnd.randint(112, 136), edge=11)

    # --- 池塘：L 形水域。一條一條橫著鋪，接縫自己就是波紋，不另外畫亮線 ---
    water, y = [], 0.012
    while y < 0.60:
        x0 = 0.47 if y < 0.235 else 0.055        # 上半段只有右邊那塊，左上是砂堤
        water.append((band(y, x0 + rnd.uniform(-.010, .014), 0.744 + rnd.uniform(-.026, .012),
                           rnd.uniform(.085, .130), None, None, skew=rnd.uniform(-.008, .008)),
                      rnd.choice((WATER, WATER2, WATER3)), rnd.randint(96, 124), 16))
        y += rnd.uniform(.055, .095)
    group(water)

    # --- 岸：上緣的砂堤＋底下的石岸，下緣沿著水線再一條。都不吃底下的草／水 ---
    group([(band(.045, .05, .476, .072, None, None, skew=.004), SAND, 132, 20),
           (band(.092, .05, .476, .070, None, None, skew=-.003), SAND, 126, 20)])
    group([(band(.146, .05, .478, .062, None, None, skew=.003), ROCK, 142, 20),
           (band(.188, .05, .478, .062, None, None, skew=-.002), ROCK, 136, 20)])
    group([(band(.574, .05, .756, .046, None, None, skew=-.010), SHORE, 144, 20),
           (band(.602, .05, .756, .044, None, None, skew=-.009), SHORE, 138, 20)])

    # --- 右側與下緣的裸土 ---
    for cx, cy, rx, ry in ((.91, .09, .15, .16), (.955, .72, .12, .18), (.70, .99, .18, .10)):
        group([(bl(cx, cy, rx, ry, wob=0.20), DIRT, 124, 18),
               (bl(cx + rx * .25, cy + ry * .2, rx * .62, ry * .6, wob=0.24), DIRT2, 110, 18)])

    # --- 左緣的柵欄木樁：每根兩條直的壓在一起，壓邊處就是木頭的暗面 ---
    for y0, y1 in ((-.02, .22), (.25, .47), (.50, .63)):
        lay(shape([(.026, y0), (.050, y0), (.050, y1), (.026, y1)]), BARK, 150, 16)
        lay(shape([(.044, y0 + .012), (.062, y0 + .012), (.062, y1 - .012),
                   (.044, y1 - .012)]), BARK, 132, 16)

    # --- 鏽紅闊葉樹：樹幹一條，樹冠五片互相壓邊，重疊處變深就是體積 ---
    lay(shape([(.140, .55), (.176, .55), (.184, .80), (.132, .80)]), BARK, 158, 16)
    lay(bl(0.157, 0.70, 0.055, 0.075, wob=0.22), BUSH, 128, 18)
    group([(bl(.150, .44, .075, .215, n=17, wob=0.19), RUST[0], 128, 18),
           (bl(.190, .38, .050, .150, n=17, wob=0.19), RUST[1], 122, 18),
           (bl(.120, .36, .045, .135, n=17, wob=0.19), RUST[2], 118, 18),
           (bl(.165, .27, .052, .110, n=17, wob=0.19), RUST[1], 120, 18),
           (bl(.145, .55, .062, .105, n=17, wob=0.19), RUST[2], 118, 18)])

    # --- 深綠針葉樹：三層三角形往上收 ---
    lay(shape([(.672, .50), (.696, .50), (.700, .68), (.668, .68)]), BARK, 155, 16)
    group([(shape([(.621, .55), (.745, .55), (.683, .34)], seg=.05), PINE[1], 136, 18),
           (shape([(.633, .40), (.733, .40), (.683, .21)], seg=.05), PINE[0], 132, 18),
           (shape([(.647, .26), (.719, .26), (.683, .08)], seg=.05), PINE[2], 128, 18)])

    # --- 青綠小樹 ---
    lay(shape([(.578, .62), (.594, .62), (.600, .80), (.572, .80)]), BARK, 150, 16)
    lay(bl(0.585, 0.792, 0.045, 0.045, wob=0.24), BUSH, 126, 18)
    group([(bl(0.585, 0.575, 0.060, 0.105, n=17, wob=0.20), TEAL[0], 126, 20),
           (bl(0.606, 0.545, 0.036, 0.065, n=15, wob=0.22), TEAL[1], 118, 20)])

    # --- 樹墩 ---
    lay(bl(0.762, 0.845, 0.032, 0.055, wob=0.18), BARK, 150, 16)
    lay(bl(0.762, 0.805, 0.028, 0.028, wob=0.20), BARK, 128, 16)

    # --- 岸邊與草地上的草叢：直接乘在草地上，本來就該是同一片草的濃處 ---
    for _ in range(30):
        lay(bl(rnd.uniform(.05, .74), rnd.uniform(.605, .695),
               rnd.uniform(.008, .017), rnd.uniform(.014, .026), n=9, wob=0.32), BUSH, 118)
    for x0, y0, x1, y1 in ((.0, .70, 1.0, 1.0), (.76, .0, 1.0, 1.0)):
        for _ in range(24):
            lay(bl(rnd.uniform(x0, x1), rnd.uniform(y0, y1),
                   rnd.uniform(.006, .013), rnd.uniform(.010, .020), n=9, wob=0.32), BUSH, 104)

    finish_flat(Image.fromarray(np.clip(cv, 0, 255).astype(np.uint8), "RGB"), (W, H), SEED, OUT)


if __name__ == "__main__":
    main()
