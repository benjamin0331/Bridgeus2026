import { useEffect, useRef, useState } from 'react';
import { toPng } from 'html-to-image';
import api from '../api/client';
import './SettlementReceipt.css';

// ---------------------------------------------------------------------------
// 數值 → 呈現的所有邏輯集中在這支，PostQuestionnairePage 只負責掛載。
// 第二頁五邊形的數值規格見 docs/settlement_radar.md。
// ---------------------------------------------------------------------------

const CHANGE_EPSILON = 0.1;

function fmt(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : '—';
}

function describeDelta(delta) {
  if (delta === null || delta === undefined) return null;
  if (Math.abs(delta) < CHANGE_EPSILON) return '立場幾乎沒有改變';
  const amount = Math.abs(delta).toFixed(2);
  return delta > 0
    ? `往「支持」方向移動了 ${amount} 分`
    : `往「反對」方向移動了 ${amount} 分`;
}

function describeCentrism(value) {
  if (value === null || value === undefined) return null;
  if (Math.abs(value) < CHANGE_EPSILON) return '極化程度沒有明顯變化';
  return value < 0 ? '更靠近中立（去極化）' : '更遠離中立（更極化）';
}

// 數字 0→目標的跳動；animate=false 時直接顯示終值（給匯出用）。
function useCountUp(target, animate) {
  const [display, setDisplay] = useState(animate ? 0 : target);
  useEffect(() => {
    const end = Number(target);
    if (!animate || !Number.isFinite(end)) return undefined;
    let raf;
    const start = performance.now();
    const tick = (now) => {
      const p = Math.min((now - start) / 900, 1);
      setDisplay(end * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, animate]);
  return display;
}

function CountNumber({ value, animate }) {
  const display = useCountUp(value, animate);
  return <>{fmt(display)}</>;
}

// ---------------------------------------------------------------------------
// SVG 圖表（全手刻，米色紙面配色）
// ---------------------------------------------------------------------------

const SEAL = '#b05540';
const GREEN = '#4c7a3c';
const GREY = '#8a7d6f';
const INK = '#2f2722';
const MUTED = '#9a8f82';

// 1–7 立場刻度尺：4=中立，前(灰)→後(印章色)兩點 + 箭頭
function StanceAxis({ sPre, sPost }) {
  const W = 320;
  const x0 = 24;
  const x1 = W - 24;
  const at = (v) => x0 + ((Math.min(Math.max(v, 1), 7) - 1) / 6) * (x1 - x0);
  const hasPre = sPre !== null && sPre !== undefined;
  const y = 46;
  const preX = hasPre ? at(sPre) : null;
  const postX = at(sPost);

  return (
    <svg className="sr-chart" viewBox={`0 0 ${W} 78`} role="img" aria-label="立場刻度">
      <line x1={x0} y1={y} x2={x1} y2={y} stroke="#d8cfc3" strokeWidth="2" strokeLinecap="round" />
      {[1, 4, 7].map((t) => (
        <g key={t}>
          <line x1={at(t)} y1={y - 5} x2={at(t)} y2={y + 5} stroke="#c7bdb0" strokeWidth="1.5" />
          <text x={at(t)} y={y + 20} textAnchor="middle" fontSize="10" fill={MUTED}>
            {t === 4 ? '中立' : t}
          </text>
        </g>
      ))}
      {hasPre && postX !== preX && (
        <line
          x1={preX} y1={y - 14} x2={postX} y2={y - 14}
          stroke={SEAL} strokeWidth="1.5" markerEnd="url(#sr-arrow)"
        />
      )}
      <defs>
        <marker id="sr-arrow" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill={SEAL} />
        </marker>
      </defs>
      {hasPre && (
        <g>
          <circle cx={preX} cy={y} r="5" fill="#fff" stroke={GREY} strokeWidth="2.5" />
          <text x={preX} y={y - 20} textAnchor="middle" fontSize="10" fill={GREY}>前 {fmt(sPre)}</text>
        </g>
      )}
      <circle cx={postX} cy={y} r="6" fill={SEAL} />
      <text x={postX} y={hasPre ? y - 34 : y - 20} textAnchor="middle" fontSize="10" fontWeight="700" fill={SEAL}>
        後 {fmt(sPost)}
      </text>
    </svg>
  );
}

// 以 0 為中心的有向長條（delta / centrism 共用）
function DivergingBar({ value, range = 3, leftColor, rightColor, leftLabel, rightLabel }) {
  if (value === null || value === undefined) return null;
  const W = 320;
  const mid = W / 2;
  const half = mid - 30;
  const v = Math.max(Math.min(value, range), -range);
  const len = (Math.abs(v) / range) * half;
  const positive = v >= 0;
  const color = positive ? rightColor : leftColor;

  return (
    <svg className="sr-chart" viewBox={`0 0 ${W} 44`} role="img" aria-label="有向長條">
      <line x1={mid} y1="6" x2={mid} y2="30" stroke="#c7bdb0" strokeWidth="1.5" />
      <rect
        x={positive ? mid : mid - len} y="12" width={Math.max(len, 1)} height="12" rx="3"
        fill={color}
      />
      <text x="6" y="40" fontSize="9.5" fill={MUTED}>{leftLabel}</text>
      <text x={W - 6} y="40" fontSize="9.5" fill={MUTED} textAnchor="end">{rightLabel}</text>
      <text x={mid} y="40" fontSize="9.5" fill={MUTED} textAnchor="middle">0</text>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// 五邊形（思辨投入）
// 軸的定義出自 docs/post_questionnaire_v1.1.md 的「Subscale 計算-五邊形圖」。
// ---------------------------------------------------------------------------

// 參與度那一軸的滿分＝18.5 輪，兩步推出來的：
//   1. 分母取組長 docs/對話輪數for彩希.docx 的兩組平均之中間值
//      (8.64 每場 Session + 13.57 每筆樣本) / 2 = 11.105 輪
//   2. 上限再由「讓達到平均輪數的人落在 3/5 半徑」反推：11.105 / 0.6 = 18.5
// 超過的截在外圈——組長樣本裡有 47/37/34 輪這種離群值（中位數只有 9），
// 不截住會把整張圖的比例拉爛。
const TURNS_FULL_MARK = 18.5;

// ponytail: 廣度先用常數佔位。後端接上「CCND 六大分類點亮數」之後，把
// breadth 的 score 換成讀那個欄位即可，其餘不用動。
const BREADTH_PLACEHOLDER = 2;
const BREADTH_MAX = 6;

const pairMean = (data, a, b) => {
  const values = [Number(data[a]), Number(data[b])].filter((v) => Number.isFinite(v));
  return values.length ? values.reduce((x, y) => x + y, 0) / values.length : null;
};

// 每軸換算成「佔自己滿分的百分比」再比較。五個軸的值域不一樣（李克特 1-7、
// 廣度 0-6、參與度是輪數），直接畫在同一個半徑上會讓形狀騙人：同樣是「剛好
// 中等」，李克特的 4 分畫成 57%、參與度剛好達平均卻只有 14%。
const normLikert = (v) => (v - 1) / 6;          // 1 -> 0、7 -> 1

const PENTAGON_AXES = [
  {
    key: 'change', label: '改變',
    raw: (d) => pairMean(d, 'exp_stance_change_1', 'exp_stance_change_2'),
    norm: normLikert,
    hint: '主觀立場改變自覺',
  },
  {
    key: 'quality', label: '品質',
    raw: (d) => pairMean(d, 'exp_quality_1', 'exp_quality_2'),
    norm: normLikert,
    hint: '對話品質感知',
  },
  {
    key: 'reflection', label: '反思',
    raw: (d) => pairMean(d, 'exp_reflection_1', 'exp_reflection_2'),
    norm: normLikert,
    hint: '自我反思 / 元認知',
  },
  {
    key: 'breadth', label: '廣度',
    raw: () => BREADTH_PLACEHOLDER,
    norm: (v) => v / BREADTH_MAX,
    hint: 'CCND 六大分類點亮數',
  },
  {
    key: 'engagement', label: '參與度',
    raw: (d) => {
      const turns = Number(d.substantive_turn_count);
      return Number.isFinite(turns) ? turns : null;
    },
    norm: (v) => Math.min(v, TURNS_FULL_MARK) / TURNS_FULL_MARK,
    hint: '實質發言輪數',
  },
];

function axisValues(data) {
  return PENTAGON_AXES.map((axis) => {
    const raw = axis.raw(data);
    return {
      ...axis,
      raw,
      ratio: raw === null ? null : Math.min(Math.max(axis.norm(raw), 0), 1),
    };
  });
}

// 四個級距＝把五軸平均後的百分比四等分。切點來自「等分可能範圍」而不是憑感覺
// 挑的分數；不顯示 S/A/B/C 字母，只給頭銜——字母暗示一套客觀評分標準，而這
// 五個軸裡有三個是自評量表，撐不起那個暗示。
const TITLE_TIERS = [
  { min: 0.75, title: '深度思辨者' },
  { min: 0.50, title: '用心對話者' },
  { min: 0.25, title: '認真參與者' },
  { min: 0, title: '初心探索者' },
];

function computeTitle(data) {
  const ratios = axisValues(data).map((a) => a.ratio).filter((r) => r !== null);
  if (ratios.length === 0) return { title: '完成對話', average: null };
  const average = ratios.reduce((a, b) => a + b, 0) / ratios.length;
  const tier = TITLE_TIERS.find((t) => average >= t.min) || TITLE_TIERS.at(-1);
  return { title: tier.title, average };
}

// 五邊形雷達。半徑用正規化後的比例，所以五個軸的外圈代表各自的滿分
// （李克特 7、廣度 6、參與度 18.5 輪）。
function PentagonRadar({ data }) {
  const size = 300;
  const c = size / 2;
  const R = 74;
  const axes = axisValues(data).map((axis, i) => {
    const ratio = axis.ratio === null ? 0 : axis.ratio;
    const angle = (Math.PI * 2 * i) / PENTAGON_AXES.length - Math.PI / 2;
    const r = ratio * R;
    return {
      ...axis,
      angle,
      x: c + Math.cos(angle) * r,
      y: c + Math.sin(angle) * r,
      lx: c + Math.cos(angle) * (R + 20),
      ly: c + Math.sin(angle) * (R + 20),
      gx: c + Math.cos(angle) * R,
      gy: c + Math.sin(angle) * R,
    };
  });
  const poly = axes.map((a) => `${a.x.toFixed(1)},${a.y.toFixed(1)}`).join(' ');

  return (
    <svg className="sr-chart" viewBox={`0 0 ${size} ${size}`} role="img" aria-label="思辨投入五邊形">
      {[0.25, 0.5, 0.75, 1].map((ring) => (
        <polygon
          key={ring}
          points={axes.map((a) => `${(c + Math.cos(a.angle) * R * ring).toFixed(1)},${(c + Math.sin(a.angle) * R * ring).toFixed(1)}`).join(' ')}
          fill="none" stroke="#e3dacd" strokeWidth="1"
        />
      ))}
      {axes.map((a) => (
        <line key={a.key} x1={c} y1={c} x2={a.gx} y2={a.gy} stroke="#e3dacd" strokeWidth="1" />
      ))}
      <polygon points={poly} fill="rgba(176,85,64,0.16)" stroke={SEAL} strokeWidth="2" strokeLinejoin="round" />
      {axes.map((a) => (
        <circle key={a.key} cx={a.x} cy={a.y} r="3" fill={SEAL} />
      ))}
      {axes.map((a) => (
        <text
          key={a.key} x={a.lx} y={a.ly}
          textAnchor={Math.abs(a.lx - c) < 8 ? 'middle' : a.lx > c ? 'start' : 'end'}
          dominantBaseline="middle" fontSize="11" fill={MUTED}
        >
          {a.label}
        </text>
      ))}
    </svg>
  );
}

// 讚 vs 倒讚 甜甜圈（識別靠圖示+數字，不靠顏色）
function ReactionsDonut({ likes, dislikes }) {
  const total = likes + dislikes;
  const size = 132;
  const c = size / 2;
  const r = 48;
  const circ = 2 * Math.PI * r;
  const likeFrac = total > 0 ? likes / total : 0;

  return (
    <div className="sr-donut">
      <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label="讚倒讚比例">
        <circle cx={c} cy={c} r={r} fill="none" stroke={GREY} strokeWidth="14" />
        {total > 0 && (
          <circle
            cx={c} cy={c} r={r} fill="none" stroke={GREEN} strokeWidth="14"
            strokeDasharray={`${circ * likeFrac} ${circ}`}
            transform={`rotate(-90 ${c} ${c})`}
          />
        )}
        <text x={c} y={c - 2} textAnchor="middle" fontSize="22" fontWeight="800" fill={INK}>{total}</text>
        <text x={c} y={c + 15} textAnchor="middle" fontSize="10" fill={MUTED}>次表態</text>
      </svg>
      <div className="sr-donut-legend">
        <span><i className="sr-swatch" style={{ background: GREEN }} />讚 <b>{likes}</b></span>
        <span><i className="sr-swatch" style={{ background: GREY }} />倒讚 <b>{dislikes}</b></span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 分頁內容
// ---------------------------------------------------------------------------

function StatRow({ label, value, tone, desc }) {
  return (
    <div className="sr-stat">
      <div className="sr-stat-head">
        <span className="sr-stat-label">{label}</span>
        <span className={`sr-stat-num sr-tone-${tone || 'flat'}`}>{value}</span>
      </div>
      {desc && <p className="sr-stat-desc">{desc}</p>}
    </div>
  );
}

function PageStance({ data, animate }) {
  const { s_pre: sPre, s_post: sPost, delta_s: delta, stance_centrism: centrism } = data;
  const hasPre = sPre !== null && sPre !== undefined;
  const deltaTone = delta === null || Math.abs(delta) < CHANGE_EPSILON ? 'flat' : delta > 0 ? 'up' : 'down';
  const centrismTone = centrism === null || Math.abs(centrism) < CHANGE_EPSILON ? 'flat' : centrism < 0 ? 'good' : 'warn';

  return (
    <div className="sr-page">
      <div className="sr-section-title">立場軌跡</div>
      <div className="sr-scores">
        <div className="sr-score">
          <span className="sr-score-label">對話前</span>
          <span className="sr-score-val">{hasPre ? <CountNumber value={sPre} animate={animate} /> : '—'}</span>
        </div>
        <span className="sr-score-arrow">→</span>
        <div className="sr-score">
          <span className="sr-score-label">對話後</span>
          <span className="sr-score-val sr-score-val--post"><CountNumber value={sPost} animate={animate} /></span>
        </div>
      </div>
      <StanceAxis sPre={hasPre ? sPre : null} sPost={sPost} />
      {hasPre ? (
        <>
          <StatRow
            label="立場移動量" value={`${delta > 0 ? '+' : ''}${fmt(delta)}`}
            tone={deltaTone} desc={describeDelta(delta)}
          />
          <DivergingBar value={delta} leftColor={GREY} rightColor={SEAL} leftLabel="偏反對" rightLabel="偏支持" />
          <StatRow
            label="去極化指標" value={`${centrism > 0 ? '+' : ''}${fmt(centrism)}`}
            tone={centrismTone} desc={describeCentrism(centrism)}
          />
          <DivergingBar value={centrism} leftColor={GREEN} rightColor={SEAL} leftLabel="更靠近中立" rightLabel="更極化" />
        </>
      ) : (
        <p className="sr-note">這場沒有前測分數，只呈現對話後立場。</p>
      )}
    </div>
  );
}

function PageEngagement({ data }) {
  const { title } = computeTitle(data);
  return (
    <div className="sr-page">
      <div className="sr-section-title">思辨投入</div>
      <div className="sr-grade">
        {/* 印章是圖不是字母：字母（S/A/B/C）暗示一套客觀評分標準，這五個軸
            有三個是自評量表，撐不起那個暗示。改用平台的青蛙印章。
            用 seal_ink.png（由 seal.png 裁掉留白 + 染成頭銜同色）：原圖內容只佔
            畫布 44%，直接顯示會小一半。 */}
        <span className="sr-seal" aria-hidden="true">
          <img className="sr-seal-img" src="/seal_ink.png" alt="" />
        </span>
        <div className="sr-grade-text">
          <span className="sr-grade-title">「{title}」</span>
        </div>
      </div>
      <PentagonRadar data={data} />
    </div>
  );
}

function PageExtras({ data, reactions }) {
  const isHH = data.experiment_condition === 'hh';
  const judgment = data.opponent_judgment; // 1真人 2AI 3不確定
  const judgmentMap = { 1: '你猜「真人」', 2: '你猜「AI」', 3: '你當時不確定' };

  return (
    <div className="sr-page">
      <div className="sr-section-title">對話花絮</div>
      <div className="sr-extra-row">
        <span className="sr-extra-label">對話對象</span>
        <span className="sr-extra-badge">{isHH ? '真人' : 'AI'}</span>
      </div>
      {!isHH && judgment != null && (
        <div className="sr-extra-row">
          <span className="sr-extra-label">你的判斷</span>
          <span className="sr-extra-badge">{judgmentMap[judgment] || '—'}</span>
        </div>
      )}
      {reactions ? (
        <ReactionsDonut likes={reactions.likes} dislikes={reactions.dislikes} />
      ) : (
        <p className="sr-note">這場沒有留下讚 / 倒讚紀錄。</p>
      )}
    </div>
  );
}

// 收據本體（可重複用於檢視與匯出）；export=true 時把每一頁全展開、不動畫。
function ReceiptBody({ data, reactions, page, export: isExport }) {
  const pages = [
    <PageStance key="s" data={data} animate={!isExport} />,
    <PageEngagement key="e" data={data} />,
    <PageExtras key="x" data={data} reactions={reactions} />,
  ];
  const topicLabel = data.topic_id ? `議題 #${data.topic_id}` : '對話';

  return (
    <div className={`sr-receipt${isExport ? ' sr-receipt--export' : ''}`}>
      <div className="sr-header">
        <div className="sr-brand">TakeABridge</div>
        <h2 className="sr-title">對話結算明細</h2>
        <div className="sr-meta">{topicLabel}</div>
        <div className="sr-perf" />
      </div>
      {isExport ? pages : pages[page]}
      <div className="sr-perf sr-perf--bottom" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 主元件：信封 → 開封 → 分頁收據 → 分享匯出
// ---------------------------------------------------------------------------

export default function SettlementReceipt({ data, sessionId, roomId, condition = 'ai' }) {
  const [phase, setPhase] = useState('sealed'); // sealed | opening | open
  const [page, setPage] = useState(0);
  const [reactions, setReactions] = useState(null);
  const [exporting, setExporting] = useState(false);
  const exportRef = useRef(null);

  // 抓這場的讚 / 倒讚數（失敗就當作沒有，不擋畫面）
  useEffect(() => {
    const targetType = condition === 'hh' ? 'match' : 'ai';
    const conversationId = condition === 'hh' ? roomId : sessionId;
    if (!conversationId) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get('/api/message-reactions/', {
          params: { target_type: targetType, conversation_id: conversationId },
        });
        if (cancelled) return;
        const list = res.data?.reactions || [];
        setReactions({
          likes: list.filter((r) => r.value === 1).length,
          dislikes: list.filter((r) => r.value === -1).length,
        });
      } catch {
        /* 讚倒讚是加分項，失敗就略過 */
      }
    })();
    return () => { cancelled = true; };
  }, [condition, roomId, sessionId]);

  const openEnvelope = () => {
    if (phase !== 'sealed') return;
    setPhase('opening');
    setTimeout(() => setPhase('open'), 850);
  };

  const handleShare = async () => {
    if (!exportRef.current || exporting) return;
    setExporting(true);
    try {
      const dataUrl = await toPng(exportRef.current, {
        pixelRatio: 2,
        backgroundColor: '#f7f3f0',
      });
      const link = document.createElement('a');
      link.download = '對話結算明細.png';
      link.href = dataUrl;
      link.click();
    } catch (error) {
      console.error('結算圖匯出失敗：', error);
    } finally {
      setExporting(false);
    }
  };

  const totalPages = 3;

  return (
    <div className="sr-page-wrap">
      {phase !== 'open' ? (
        <button
          type="button"
          className={`sr-envelope${phase === 'opening' ? ' is-opening' : ''}`}
          onClick={openEnvelope}
          aria-label="開啟對話結算"
        >
          <svg className="sr-env-svg" viewBox="0 0 240 168" fill="none" aria-hidden="true">
            <rect className="sr-env-body" x="6" y="40" width="228" height="120" rx="10" />
            <path className="sr-env-fold" d="M8 150 L120 96 L232 150" />
            <path className="sr-env-flap" d="M8 44 L120 112 L232 44" />
          </svg>
          <span className="sr-env-hint">點擊開啟你的對話結算</span>
        </button>
      ) : (
        <div className="sr-open">
          <ReceiptBody data={data} reactions={reactions} page={page} />

          <div className="sr-controls">
            <button
              type="button" className="sr-nav" disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              ◀
            </button>
            <div className="sr-dots">
              {Array.from({ length: totalPages }, (_, i) => (
                <span key={i} className={`sr-dot${i === page ? ' active' : ''}`} />
              ))}
            </div>
            <button
              type="button" className="sr-nav" disabled={page === totalPages - 1}
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            >
              ▶
            </button>
          </div>

          <div className="sr-actions">
            <button type="button" className="sr-share" onClick={handleShare} disabled={exporting}>
              <img src="/forward.png" alt="" className="sr-share-icon" />
              {exporting ? '產生圖片中…' : '分享 / 下載長圖'}
            </button>
          </div>
        </div>
      )}

      {/* 隱藏的匯出容器：三頁全展開，供 html-to-image 一次截成長圖 */}
      <div className="sr-export-holder" aria-hidden="true">
        <div ref={exportRef}>
          <ReceiptBody data={data} reactions={reactions} export />
        </div>
      </div>
    </div>
  );
}
