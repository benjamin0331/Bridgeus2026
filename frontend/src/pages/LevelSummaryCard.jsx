import { useEffect, useState } from 'react';
import api from '../api/client';

// 等級 → 顏色名（純文字用，例如「黃色青蛙」）。畫面上的青蛙是真的 sprite 圖，由
// scripts/export_web_assets.py 從 Godot 素材匯出到 public/frogs/lvN.png，所以顏色沒有
// 在這裡複製一份 —— 改配色只要重跑那支腳本，網頁就跟著變。
// 門檻也不在這裡寫死，是從 /api/titles/me/ 的 level_thresholds 讀的
// （後端 views.py::LEVEL_THRESHOLDS 是唯一來源）。
const LEVEL_NAMES = ['白', '紅', '橙', '黃', '綠', '藍', '紫'];

const HINT = '等級隨完成的對話場次而提升，可以在 Godot 中解鎖不同形象的青蛙';

// 後端還沒串上時顯示的示意資料，讓版面看得到、不會整張消失。
// ⚠️ 串好之後把這個常數和下面的 isPlaceholder 標籤一起刪掉（見 docs/0804.md §2.1）。
const PLACEHOLDER = {
  level: 2,
  dialogue_count: 7,
  level_thresholds: [0, 2, 5, 10, 17, 27, 40],
};

function LevelSummaryCard() {
  const [data, setData] = useState(null);
  const [isPlaceholder, setIsPlaceholder] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // 等級跟頭銜同一支端點（後端刻意順路回傳，見 docs/godot_level_colors.md §三）。
    api.get('/api/titles/me/')
      .then((response) => {
        if (cancelled) return;
        // 後端還沒部署新欄位時 level 會是 undefined，那也算沒串上。
        if (response.data?.level === undefined) {
          setData(PLACEHOLDER);
          setIsPlaceholder(true);
          return;
        }
        setData(response.data);
      })
      .catch((error) => {
        if (cancelled) return;
        // 不要靜靜消失：console 留線索，畫面退回示意資料。
        console.warn('等級資料讀取失敗，顯示示意資料：', error?.message ?? error);
        setData(PLACEHOLDER);
        setIsPlaceholder(true);
      });
    return () => { cancelled = true; };
  }, []);

  // 第一次 render（請求還沒回來）先用示意資料佔位，避免版面跳動。
  const source = data ?? PLACEHOLDER;
  const level = Number(source.level ?? 0);
  const count = Number(source.dialogue_count ?? 0);
  const thresholds = Array.isArray(source.level_thresholds) ? source.level_thresholds : [];
  const color = { name: LEVEL_NAMES[level] ?? String(level) };
  // 最高級沒有「下一級」，remaining 保持 null → 不顯示進度那一段。
  const nextThreshold = level + 1 < thresholds.length ? Number(thresholds[level + 1]) : null;
  const remaining = nextThreshold === null ? null : Math.max(nextThreshold - count, 0);

  return (
    <section className="level-summary">
      <img
        className="level-summary-frog"
        src={`/frogs/lv${level}.png`}
        alt={`Lv${level} ${color.name}色青蛙`}
      />

      <div className="level-summary-block">
        <span className="level-summary-label">
          Godot 等級
          <button
            type="button"
            className="level-summary-info"
            aria-label={HINT}
            title={HINT}
          >
            ?
          </button>
          <span className="level-summary-tip" role="tooltip">{HINT}</span>
        </span>
        <span className="level-summary-value">
          Lv{level}
          <span className="level-summary-unit">{color.name}色青蛙</span>
        </span>
      </div>

      <div className="level-summary-block">
        <span className="level-summary-label">已完成對話</span>
        <span className="level-summary-value">
          {count}
          <span className="level-summary-unit">場</span>
        </span>
      </div>

      {remaining !== null && (
        <div className="level-summary-block">
          <span className="level-summary-label">離 Lv{level + 1}</span>
          <span className="level-summary-value">
            {remaining}
            <span className="level-summary-unit">場</span>
          </span>
        </div>
      )}

      {isPlaceholder && (
        <span className="level-summary-placeholder">示意資料 · 後端尚未串接</span>
      )}
    </section>
  );
}

export default LevelSummaryCard;
