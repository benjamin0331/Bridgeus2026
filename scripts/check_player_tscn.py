"""驗 player_00.tscn 的等級動畫接線是否完整。

Godot 專案沒有自動測試，而 player_00.tscn 的 lvN 動畫是用腳本／regex 加進去的
（14 個動畫 + 105 個 AtlasTexture + 14 個 ext_resource，手改必出錯）。這支做靜態
結構檢查：id 不重複、引用都有定義、沒有孤立資源、幀數正確、素材檔存在。

它「不是」Godot 認可 —— 真正的驗證是在編輯器開一次場景。但這支能在編輯器之外
擋掉絕大多數手改造成的破壞。

    python scripts/check_player_tscn.py
"""
import collections
import re
import sys
from pathlib import Path

TSCN = Path("godot/Entities/player/player_00.tscn")
SPRITE = "godot/Assets/ToxicFrog/Level/Frog_Lv%d_%s.png"
FRAMES = {"idle": ("Idle", 8), "run": ("Hop", 7)}   # 與原素材一致：Idle 8 格、Hop 7 格


def main() -> int:
    s = TSCN.read_text(encoding="utf-8")
    errs = []

    ext_def = re.findall(r'^\[ext_resource [^\]]*id="([^"]+)"\]', s, re.M)
    sub_def = re.findall(r'^\[sub_resource [^\]]*id="([^"]+)"\]', s, re.M)
    ext_use = set(re.findall(r'ExtResource\("([^"]+)"\)', s))
    sub_use = set(re.findall(r'SubResource\("([^"]+)"\)', s))

    dup = [k for k, v in collections.Counter(ext_def + sub_def).items() if v > 1]
    if dup:
        errs.append(f"重複的 resource id: {dup}")
    missing_ext = ext_use - set(ext_def)
    if missing_ext:
        errs.append(f"引用了不存在的 ExtResource: {sorted(missing_ext)}")
    missing_sub = sub_use - set(sub_def)
    if missing_sub:
        errs.append(f"引用了不存在的 SubResource: {sorted(missing_sub)}")
    # Script 資源由 node 的 script= 引用，不走 ExtResource() 呼叫形式，所以豁免
    orphan = {e for e in ext_def if e not in ext_use and not e.startswith("1_")}
    if orphan:
        errs.append(f"有 ext_resource 沒被任何地方用到: {sorted(orphan)}")

    # 等級數從 0 依序探測到缺號為止，跟 player_00.gd::level_count() 同一個原則。
    # 非連號的 lvN（lv777 彩虹蛙彩蛋）刻意不算進等級，只列出來讓人知道它在那裡。
    anims = re.findall(r'"name": &"([^"]+)"', s)
    numbered = sorted(int(m.group(1)) for m in
                      (re.fullmatch(r"lv(\d+)_idle", a) for a in anims) if m)
    levels = []
    while len(levels) in numbered:
        levels.append(len(levels))
    if not levels:
        errs.append("場景裡找不到 lv0_idle，等級動畫沒接上")
    extras = [n for n in numbered if n not in levels]

    # 一次切出所有動畫的幀數。必須用 finditer 從頭掃：對單一名稱做 re.search 的話，
    # lazy 的 (.*?) 會從檔頭一路吃到那個名稱，算出來的幀數是整個陣列的總和。
    frame_counts = {
        m.group(2): m.group(1).count('"duration"')
        for m in re.finditer(
            r'"frames": \[(.*?)\],\n"loop": 1,\n"name": &"([^"]+)"', s, re.S)
    }

    for lv in levels:
        for suffix, (filename, want) in FRAMES.items():
            name = f"lv{lv}_{suffix}"
            if name not in frame_counts:
                errs.append(f"缺少動畫 {name}")
            elif frame_counts[name] != want:
                errs.append(f"{name} 有 {frame_counts[name]} 格，應該 {want}")
            p = Path(SPRITE % (lv, filename))
            if not p.exists() or p.stat().st_size == 0:
                errs.append(f"素材缺失或空檔: {p}")

    if errs:
        print("FAIL")
        for e in errs:
            print("  -", e)
        return 1
    print(f"ok: Lv0–Lv{levels[-1]}（{len(levels)} 級）"
          f"／{len(ext_def)} ext_resource／{len(sub_def)} sub_resource／{len(anims)} animations")
    if extras:
        print(f"    另有未接進等級的額外動畫: {['lv%d' % n for n in extras]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
