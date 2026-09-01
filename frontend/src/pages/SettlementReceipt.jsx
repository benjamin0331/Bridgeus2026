import { useEffect, useRef, useState } from 'react';
import { toPng } from 'html-to-image';
import api from '../api/client';
import './SettlementReceipt.css';

// ---------------------------------------------------------------------------
// 數值 → 呈現的所有邏輯集中在這支，PostQuestionnairePage 只負責掛載。
// 資料來源見 docs/settlement_screen.md。
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

// 評級只看與立場方向無關的投入型量表，避免獎勵改立場而污染實驗。
// ponytail: 門檻憑自評量表偏高的經驗值，太鬆/太嚴改這裡即可。
const ENGAGEMENT_AXES = [
  { key: 'exp_reflection_1', label: '反思Ⅰ' },
  { key: 'exp_reflection_2', label: '反思Ⅱ' },
  { key: 'exp_quality_1', label: '品質Ⅰ' },
  { key: 'exp_quality_2', label: '品質Ⅱ' },
  { key: 'ccnd_attention', label: '注意' },
  { key: 'ccnd_awareness', label: '覺察' },
  { key: 'ccnd_influence', label: '調整' },
];

const GRADE_TIERS = [
  { min: 6.0, grade: 'S', title: '深度思辨者' },
  { min: 5.0, grade: 'A', title: '用心對話者' },
  { min: 4.0, grade: 'B', title: '認真參與者' },
  { min: 0, grade: 'C', title: '初心探索者' },
];

function computeGrade(data) {
  const scores = ENGAGEMENT_AXES
    .map((axis) => Number(data[axis.key]))
    .filter((v) => Number.isFinite(v));
  if (scores.length === 0) return { grade: '✓', title: '完成對話', average: null };
  const average = scores.reduce((s, v) => s + v, 0) / scores.length;
  const tier = GRADE_TIERS.find((t) => average >= t.min) || GRADE_TIERS.at(-1);
  return { grade: tier.grade, title: tier.title, average };
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

// 7 軸思辨投入雷達
function EngagementRadar({ data }) {
  const size = 220;
  const c = size / 2;
  const R = 78;
  const axes = ENGAGEMENT_AXES.map((axis, i) => {
    const raw = Number(data[axis.key]);
    const value = Number.isFinite(raw) ? raw : 0;
    const angle = (Math.PI * 2 * i) / ENGAGEMENT_AXES.length - Math.PI / 2;
    const r = (Math.min(Math.max(value, 0), 7) / 7) * R;
    return {
      ...axis,
      value,
      angle,
      x: c + Math.cos(angle) * r,
      y: c + Math.sin(angle) * r,
      lx: c + Math.cos(angle) * (R + 16),
      ly: c + Math.sin(angle) * (R + 16),
      gx: c + Math.cos(angle) * R,
      gy: c + Math.sin(angle) * R,
    };
  });
  const poly = axes.map((a) => `${a.x.toFixed(1)},${a.y.toFixed(1)}`).join(' ');

  return (
    <svg className="sr-chart" viewBox={`0 0 ${size} ${size}`} role="img" aria-label="思辨投入雷達">
      {[0.25, 0.5, 0.75, 1].map((ring) => (
        <circle key={ring} cx={c} cy={c} r={R * ring} fill="none" stroke="#e3dacd" strokeWidth="1" />
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
          dominantBaseline="middle" fontSize="10" fill={MUTED}
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
  const { grade, title } = computeGrade(data);
  return (
    <div className="sr-page">
      <div className="sr-section-title">思辨投入</div>
      <div className={`sr-grade sr-grade-${grade}`}>
        <div className="sr-seal">{grade}</div>
        <div className="sr-grade-text">
          <span className="sr-grade-caption">思辨投入評級</span>
          <span className="sr-grade-title">「{title}」</span>
        </div>
      </div>
      <EngagementRadar data={data} />
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

// 收據本體（可重複用於檢視與匯出）；export=true 時把三頁全展開、不動畫。
function ReceiptBody({ data, reactions, page, export: isExport }) {
  const pages = [
    <PageStance key="s" data={data} animate={!isExport} />,
    <PageEngagement key="e" data={data} />,
    <PageExtras key="x" data={data} reactions={reactions} />,
  ];
  // 後端補的 topic_title 是議題名；舊資料或未知議題才退回 id。
  const topicLabel = data.topic_title
    || (data.topic_id ? `議題 #${data.topic_id}` : '對話');

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
