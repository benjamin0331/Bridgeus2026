"""膠帶拼貼的共用零件：紙、撕邊多邊形、接觸陰影、出圖。

gen_kb_tape.py（書牆）和 gen_map_tape.py（遊戲地圖）兩張圖只有「畫什麼」不一樣，
紙紋、陰影、雜訊、量化這些像素層的處理完全共用，放這裡免得兩邊各改各的、久了兩張
圖的質感就對不起來。

⚠️ paper_layer 與 finish 消耗亂數的順序不能動：兩支腳本的種子是固定的，改了順序
整張圖的元素就全部換位置，之前產好的 PNG 再也對不回來。
"""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

PAPER = (247, 242, 228)      # 暖白日誌紙，不是米黃也不是純白


def paper_layer(rnd, w, h, ss, seed):
    """暖白日誌紙：細纖維雜訊 ＋ 少量較長的柔軟纖維，不做大面積髒污。w/h 是超取樣後的尺寸。"""
    base = np.zeros((h, w, 3), np.float32) + np.array(PAPER, np.float32)
    base += np.random.default_rng(seed).normal(0, 3.4, (h, w, 1))

    threads = Image.new("L", (w, h), 128)
    td = ImageDraw.Draw(threads)
    for _ in range(90):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        a, ln = rnd.uniform(0, math.pi), rnd.uniform(18, 90) * ss
        td.line([x, y, x + math.cos(a) * ln, y + math.sin(a) * ln],
                fill=128 + rnd.choice([-16, 14]), width=1)
    threads = threads.filter(ImageFilter.GaussianBlur(0.7))
    base += (np.asarray(threads, np.float32)[:, :, None] - 128) * 0.55
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")


def torn(pts, amp, rnd, seg=26.0):
    """把多邊形的每條邊細分後往法線方向亂推一點點：手撕膠帶的邊。

    seg 是細分的目標段長；邊越長切越多刀，短邊才不會被推爛。
    """
    out = []
    for i, (x0, y0) in enumerate(pts):
        x1, y1 = pts[(i + 1) % len(pts)]
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln, dx / ln
        out.append((x0, y0))
        for k in range(1, max(1, int(ln / seg))):
            t = k / max(1, int(ln / seg))
            j = rnd.uniform(-amp, amp)
            out.append((x0 + dx * t + nx * j, y0 + dy * t + ny * j))
    return out


def blob(cx, cy, rx, ry, rnd, n=15, wob=0.16):
    """一團橢圓形的撕邊膠帶：樹冠、草叢、泥地都用它。"""
    return [(cx + math.cos(a) * rx * (1 + rnd.uniform(-wob, wob)),
             cy + math.sin(a) * ry * (1 + rnd.uniform(-wob, wob)))
            for a in (i * 2 * math.pi / n + rnd.uniform(-0.06, 0.06) for i in range(n))]


def jitter(color, rnd, amp=10):
    """每片膠帶自己的色偏：同一捲和紙撕下來也不會完全同色。"""
    return tuple(max(0, min(255, v + rnd.randint(-amp, amp))) for v in color)


def finish(paper, tapes, size, ss, seed, out_path, flip=False, colors=128):
    """接觸陰影 → 疊紙 → 交給 finish_flat。給「不透明膠帶疊在紙上」那種畫法用。"""
    # 整層膠帶的 alpha 糊開後往右下偏一點點：貼在紙上，不是浮在上面
    shade = tapes.getchannel("A").filter(ImageFilter.GaussianBlur(2.2 * ss))
    shade = shade.point(lambda v: int(v * 0.30))
    shadow = Image.new("RGBA", tapes.size, (72, 58, 40, 0))
    shadow.putalpha(shade.transform(shade.size, Image.AFFINE, (1, 0, -2 * ss, 0, 1, -3 * ss)))

    img = Image.alpha_composite(paper.convert("RGBA"), shadow)
    img = Image.alpha_composite(img, tapes).convert("RGB")
    finish_flat(img, size, seed, out_path, flip=flip, colors=colors)


def finish_flat(img, size, seed, out_path, flip=False, colors=128):
    """縮回原尺寸 → 乘算雜訊 → 量化存檔。size 是最終 (W, H)。"""
    w, h = size
    # BOX 而不是 LANCZOS：超取樣剛好是整數倍，BOX 就是乾淨的 2×2 平均。LANCZOS 會在
    # 高反差邊緣過衝，每個深色元素周圍都會鑲一圈亮邊，看起來像挖破了紙。
    img = img.resize((w, h), Image.BOX)

    # 很淡的乘算雜訊：膠帶的纖維與色料不勻，整張一起上才不會像色塊
    arr = np.asarray(img, np.float32)
    arr *= 1 + np.random.default_rng(seed + 1).normal(0, 0.016, (h, w, 1))
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    if flip:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)

    # 128 色調色盤、不抖動：紙紋與斑駁已經由上面的雜訊提供，再抖動只會讓 PNG 壓不動。
    img.quantize(colors=colors, method=Image.MEDIANCUT, dither=Image.NONE).save(
        out_path, optimize=True)
    print(f"{out_path} ({out_path.stat().st_size // 1024} KB)")


def stick(layer, pts, color, alpha):
    """把一片膠帶貼到 RGBA 圖層上，只影響它自己的外框範圍。

    不用 ImageDraw.Draw(im, "RGBA")：那個模式混色時會把目的地的 alpha 直接覆寫成
    來源的 alpha，所以一片半透明膠帶壓在不透明底色上，底下會被打出一個洞、露出紙。
    畫大面積場景時整片色塊會被洗白。paste + L 遮罩才是正確的「疊上去」：RGB 混色，
    alpha 往 255 靠。

    （gen_kb_tape.py 沒改用這個。書牆那張刻意讓紙從半透明的膠帶透上來，就是靠上面
    那個覆寫行為做出來的，換掉質感會變。）
    """
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = max(0, int(min(xs)) - 2), max(0, int(min(ys)) - 2)
    x1, y1 = min(layer.width, int(max(xs)) + 3), min(layer.height, int(max(ys)) + 3)
    if x1 <= x0 or y1 <= y0:
        return
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in pts], fill=alpha)
    layer.paste(color + (255,), (x0, y0), mask)


def multiply(canvas, pts, color, alpha, edge=0):
    """把一片膠帶乘算到 float32 的畫布上（canvas 是 H×W×3、0~255）。

    和紙是透光的：看到的顏色是「紙的顏色 × 膠帶的透光率」，疊兩層就乘兩次。所以這裡
    的 color 不是「要畫成什麼顏色」而是透光率 —— 給淺一點的值，疊出來才會輕透，重疊
    處也才會自己變深。這是膠帶感的來源，用不透明色塊貼是做不出來的。

    edge > 0 時沿著外框再乘一道細邊：膠帶裁切面那條看得見的痕。
    """
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = max(0, int(min(xs)) - 3), max(0, int(min(ys)) - 3)
    x1 = min(canvas.shape[1], int(max(xs)) + 4)
    y1 = min(canvas.shape[0], int(max(ys)) + 4)
    if x1 <= x0 or y1 <= y0:
        return
    local = [(x - x0, y - y0) for x, y in pts]
    m = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(m).polygon(local, fill=alpha)
    if edge:
        ImageDraw.Draw(m).line(local + [local[0]], fill=min(255, alpha + edge), width=3)
    m = np.asarray(m, np.float32)[:, :, None] / 255.0
    canvas[y0:y1, x0:x1] *= 1.0 - m * (1.0 - np.array(color, np.float32) / 255.0)
