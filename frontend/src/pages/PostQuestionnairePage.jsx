import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import api from '../api/client';
import './PostQuestionnairePage.css';

// --- Static question data -----------------------------------------------

const C1_QUESTIONS = [
  { index: 1, preQ: 'Q8', text: '在再生能源尚無法滿足基載電力需求的過渡期，核電是必要的橋接方案。' },
  { index: 2, preQ: 'Q5', text: '考量台灣的地震與海嘯風險，核電廠的存在對周邊居民構成不可接受的威脅。', reverse: true },
  { index: 3, preQ: 'Q3', text: '與其他能源相比，核電在發電成本與供電穩定性上具有明顯優勢。' },
  { index: 4, preQ: 'Q7', text: '核電是目前能大規模穩定供電的低碳能源中，最務實可行的選項。' },
  { index: 5, preQ: 'Q1', text: '我認為台灣現有的核電技術與管理能力，足以確保核電廠的安全運轉。' },
  { index: 6, preQ: 'Q4', text: '台灣應優先發展再生能源，而非依賴核電來達成淨零碳排目標。', reverse: true },
  { index: 7, preQ: 'Q6', text: '核電廠的建設、維護與除役成本被嚴重低估，實際上並不划算。', reverse: true },
  { index: 8, preQ: 'Q2', text: '核廢料的長期處置風險，使核電不應被視為環保的能源選項。', reverse: true },
];

const C2_QUESTIONS = [
  { key: 'exp_stance_change_1', text: '經過這次對話，我對核電議題的看法有了一些改變。' },
  { key: 'exp_stance_change_2', text: '我現在比對話前更能理解對方立場的合理之處。' },
  { key: 'exp_quality_1', text: '這次對話過程是理性且有建設性的。' },
  { key: 'exp_quality_2', text: '對話中我有感受到被尊重，而非被攻擊。' },
  { key: 'exp_reflection_1', text: '在對話過程中，我曾重新檢視過自己論點的邏輯是否充分。' },
  { key: 'exp_reflection_2', text: '這次對話讓我意識到自己在某些面向的認知可能不夠完整。' },
];

const C3_QUESTIONS = [
  { key: 'ccnd_attention', text: '在對話過程中，我有注意到並觀看了概念認知網路圖（思維導圖）。' },
  { key: 'ccnd_awareness', text: '觀看概念認知網路圖讓我更清楚地意識到自己與對方觀點之間的差異。' },
  { key: 'ccnd_influence', text: '概念認知網路圖的變化，促使我在對話中調整了自己的表達或思考方式。' },
];

const SCALE_VALUES = [1, 2, 3, 4, 5, 6, 7];
const TOTAL_STEPS = 6;

// --- Sub-components -------------------------------------------------------

function StepIndicator({ current, total }) {
  return (
    <div className="pq-step-indicator">
      {Array.from({ length: total }, (_, i) => (
        <div
          key={i}
          className={`pq-step-dot ${i < current ? 'done' : ''} ${i === current ? 'active' : ''}`}
        />
      ))}
      <span className="pq-step-label">{current + 1} / {total}</span>
    </div>
  );
}

function LikertItem({ label, text, value, onChange, tag }) {
  return (
    <div className="pq-survey-item">
      <h4>{label}{tag ? <span className="pq-tag">{tag}</span> : null}</h4>
      <p>{text}</p>
      <div className="pq-likert-scale">
        <div className="pq-likert-line" />
        {SCALE_VALUES.map((v) => (
          <div
            key={v}
            className={`pq-likert-dot ${value === v ? 'selected' : ''}`}
            onClick={() => onChange(v)}
            role="radio"
            aria-checked={value === v}
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && onChange(v)}
          />
        ))}
      </div>
      <div className="pq-likert-labels">
        <span>非常不同意</span>
        <span>非常同意</span>
      </div>
    </div>
  );
}

// --- Steps ----------------------------------------------------------------

function StepC1({ answers, onChange }) {
  return (
    <div className="pq-step-content">
      <div className="pq-part-header">
        <h3>Part C-1：立場重測</h3>
        <p>以下題目與先前問卷相同，請依照你目前的想法重新作答（1＝非常不同意，7＝非常同意）。</p>
      </div>
      {C1_QUESTIONS.map((q) => (
        <LikertItem
          key={q.index}
          label={`C1-${q.index}`}
          tag={q.reverse ? '(反向)' : null}
          text={q.text}
          value={answers[q.index]}
          onChange={(v) => onChange(q.index, v)}
        />
      ))}
    </div>
  );
}

function StepC2({ answers, onChange }) {
  return (
    <div className="pq-step-content">
      <div className="pq-part-header">
        <h3>Part C-2：體驗量表</h3>
        <p>請評估這次對話的主觀感受（1＝非常不同意，7＝非常同意）。</p>
      </div>
      {C2_QUESTIONS.map((q, i) => (
        <LikertItem
          key={q.key}
          label={`C2-${i + 1}`}
          text={q.text}
          value={answers[q.key]}
          onChange={(v) => onChange(q.key, v)}
        />
      ))}
    </div>
  );
}

function StepC3({ answers, onChange }) {
  return (
    <div className="pq-step-content">
      <div className="pq-part-header">
        <h3>Part C-3：CCND 影響評估</h3>
        <p>關於對話中顯示的概念認知網路圖（思維導圖）（1＝非常不同意，7＝非常同意）。</p>
      </div>
      {C3_QUESTIONS.map((q, i) => (
        <LikertItem
          key={q.key}
          label={`C3-${i + 1}`}
          text={q.text}
          value={answers[q.key]}
          onChange={(v) => onChange(q.key, v)}
        />
      ))}
    </div>
  );
}

function StepC4({ value, onChange }) {
  const options = [
    { value: 1, label: '真人使用者' },
    { value: 2, label: 'AI 程式' },
    { value: 3, label: '不確定' },
  ];
  return (
    <div className="pq-step-content">
      <div className="pq-part-header">
        <h3>Part C-4：對話對象判斷</h3>
        <p>在這次對話中，你認為你的對話對象是：</p>
      </div>
      <div className="pq-survey-item">
        <div className="pq-radio-group">
          {options.map((opt) => (
            <label key={opt.value} className="pq-radio-label">
              <input
                type="radio"
                name="opponent_judgment"
                value={opt.value}
                checked={value === opt.value}
                onChange={() => onChange(opt.value)}
              />
              {opt.label}
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

function StepD({ d1, d2, onD1Change, onD2Change }) {
  const d1Length = d1.trim().length;
  const d1Valid = d1Length >= 50;
  return (
    <div className="pq-step-content">
      <div className="pq-part-header">
        <h3>Part D：開放式問題</h3>
      </div>
      <div className="pq-survey-item pq-survey-item-open">
        <h4>D1 | 對立觀點陳述</h4>
        <p>
          經過這次對話，你認為反對（或支持）核電的人，他們最有力的論點是什麼？
          請試著用他們的角度來陳述。
        </p>
        <textarea
          className={`pq-open-textarea ${!d1Valid && d1.length > 0 ? 'invalid' : ''}`}
          placeholder="請用對方的角度陳述其最有力的論點（最低 50 字）"
          value={d1}
          onChange={(e) => onD1Change(e.target.value)}
          rows={6}
        />
        <div className={`pq-char-count ${d1Valid ? 'ok' : 'warn'}`}>
          {d1Length} 字{d1Valid ? '　✓' : '　（至少需 50 字）'}
        </div>
      </div>
      <div className="pq-survey-item pq-survey-item-open">
        <h4>D2 | 自由回饋</h4>
        <p>對於這次對話體驗，你有什麼想法或建議？（可自由書寫）</p>
        <textarea
          className="pq-open-textarea"
          placeholder="自由填寫，無字數限制"
          value={d2}
          onChange={(e) => onD2Change(e.target.value)}
          rows={4}
        />
      </div>
    </div>
  );
}

function StepE({ flag, detail, onFlagChange, onDetailChange }) {
  return (
    <div className="pq-step-content">
      <div className="pq-part-header">
        <h3>Part E：不適回報</h3>
        <p>在這次對話過程中，你是否曾感到不適、受到冒犯或受到不當對待？</p>
      </div>
      <div className="pq-survey-item">
        <div className="pq-radio-group">
          <label className="pq-radio-label">
            <input
              type="radio"
              name="discomfort"
              checked={!flag}
              onChange={() => onFlagChange(false)}
            />
            否，我沒有感到不適
          </label>
          <label className="pq-radio-label">
            <input
              type="radio"
              name="discomfort"
              checked={flag}
              onChange={() => onFlagChange(true)}
            />
            是，我曾感到不適
          </label>
        </div>
        {flag && (
          <div className="pq-discomfort-detail">
            <p className="pq-discomfort-hint">
              請簡述你感到不適的情況，我們會認真處理你的回饋。
            </p>
            <textarea
              className={`pq-open-textarea ${flag && !detail.trim() ? 'invalid' : ''}`}
              placeholder="請描述不適情況..."
              value={detail}
              onChange={(e) => onDetailChange(e.target.value)}
              rows={4}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// --- Result card ----------------------------------------------------------

// delta_s / stance_centrism are rounded to 4dp on the backend; treat anything
// within this band as "essentially unchanged" for the plain-language label.
const CHANGE_EPSILON = 0.1;

function fmt(n) {
  return typeof n === 'number' ? n.toFixed(2) : '—';
}

function describeDelta(delta) {
  if (delta === null || delta === undefined) return null;
  if (Math.abs(delta) < CHANGE_EPSILON) return { tone: 'flat', text: '立場幾乎沒有改變' };
  const amount = Math.abs(delta).toFixed(2);
  return delta > 0
    ? { tone: 'up', text: `往「支持」方向移動了 ${amount} 分` }
    : { tone: 'down', text: `往「反對」方向移動了 ${amount} 分` };
}

function describeCentrism(c) {
  if (c === null || c === undefined) return null;
  if (Math.abs(c) < CHANGE_EPSILON) return { tone: 'flat', text: '極化程度沒有明顯變化' };
  return c < 0
    ? { tone: 'good', text: '你變得更靠近中立 —— 出現去極化' }
    : { tone: 'warn', text: '你變得更遠離中立 —— 立場更極化了' };
}

function ResultCard({ data, onContinue }) {
  const sPre = data.s_pre;
  const sPost = data.s_post;
  const delta = data.delta_s;
  const centrism = data.stance_centrism;
  const hasPre = sPre !== null && sPre !== undefined;

  const deltaInfo = describeDelta(delta);
  const centrismInfo = describeCentrism(centrism);

  return (
    <div className="pq-page">
      <div className="pq-container">
        <div className="pq-header">
          <h2>本次對話結果</h2>
          <p className="pq-subtitle">
            以下是根據你「對話前」與「對話後」立場問卷計算出的數值（分數 1–7，4 為中立）。
          </p>
        </div>

        <div className="pq-body">
          {!hasPre && (
            <p className="pq-result-note">
              找不到你這個議題的對話前立場基準，因此無法計算立場變化。以下僅顯示對話後的立場分數。
            </p>
          )}

          <div className="pq-result-scores">
            <div className="pq-score-box">
              <span className="pq-score-label">對話前立場</span>
              <span className="pq-score-value">{hasPre ? fmt(sPre) : '—'}</span>
            </div>
            <div className="pq-score-arrow">→</div>
            <div className="pq-score-box">
              <span className="pq-score-label">對話後立場</span>
              <span className="pq-score-value">{fmt(sPost)}</span>
            </div>
          </div>

          {hasPre && (
            <div className="pq-result-metrics">
              <div className={`pq-metric pq-metric-${deltaInfo?.tone || 'flat'}`}>
                <div className="pq-metric-head">
                  <span className="pq-metric-name">立場移動量</span>
                  <span className="pq-metric-num">
                    {delta > 0 ? '+' : ''}{fmt(delta)}
                  </span>
                </div>
                <p className="pq-metric-desc">{deltaInfo?.text}</p>
              </div>

              <div className={`pq-metric pq-metric-${centrismInfo?.tone || 'flat'}`}>
                <div className="pq-metric-head">
                  <span className="pq-metric-name">去極化指標</span>
                  <span className="pq-metric-num">
                    {centrism > 0 ? '+' : ''}{fmt(centrism)}
                  </span>
                </div>
                <p className="pq-metric-desc">{centrismInfo?.text}</p>
              </div>
            </div>
          )}

          <p className="pq-result-hint">
            「立場移動量」是後測減前測分數；「去極化指標」為負代表你更靠近中立立場。
            這些數值僅供你參考，沒有好壞之分。
          </p>
        </div>

        <div className="pq-footer">
          <div className="pq-nav-buttons">
            <button className="pq-btn pq-btn-primary" onClick={onContinue}>
              繼續
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// --- Main page ------------------------------------------------------------

export default function PostQuestionnairePage() {
  const location = useLocation();
  const navigate = useNavigate();
  const {
    topicId,
    sessionId,
    roomId,
    condition = 'ai',
  } = location.state || {};

  const isHH = condition === 'hh';

  const [step, setStep] = useState(0);
  const [c1Answers, setC1Answers] = useState({});
  const [c2Answers, setC2Answers] = useState({});
  const [c3Answers, setC3Answers] = useState({});
  const [c4Answer, setC4Answer] = useState(null);
  const [d1, setD1] = useState('');
  const [d2, setD2] = useState('');
  const [discomfortFlag, setDiscomfortFlag] = useState(false);
  const [discomfortDetail, setDiscomfortDetail] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [result, setResult] = useState(null);

  // H-H 組跳過 C-4，實際步驟比較少
  // Steps: 0=C1, 1=C2, 2=C3, 3=C4(ai only)/D(hh), 4=D(ai)/E(hh), 5=E(ai)
  const actualSteps = isHH ? 5 : TOTAL_STEPS;

  const getStepLabel = () => {
    if (step === 0) return 'C-1 立場重測';
    if (step === 1) return 'C-2 體驗量表';
    if (step === 2) return 'C-3 CCND 評估';
    if (!isHH && step === 3) return 'C-4 對話對象';
    if (isHH && step === 3) return 'D 開放問題';
    if (!isHH && step === 4) return 'D 開放問題';
    return 'E 不適回報';
  };

  const canAdvance = () => {
    if (step === 0) return C1_QUESTIONS.every((q) => c1Answers[q.index] !== undefined);
    if (step === 1) return C2_QUESTIONS.every((q) => c2Answers[q.key] !== undefined);
    if (step === 2) return C3_QUESTIONS.every((q) => c3Answers[q.key] !== undefined);
    if (!isHH && step === 3) return c4Answer !== null;
    const dStep = isHH ? 3 : 4;
    if (step === dStep) return d1.trim().length >= 50;
    const eStep = isHH ? 4 : 5;
    if (step === eStep) return !discomfortFlag || discomfortDetail.trim().length > 0;
    return true;
  };

  const handleNext = () => {
    if (!canAdvance()) {
      setSubmitError('請填完本頁所有欄位再繼續。');
      return;
    }
    setSubmitError('');
    setStep((s) => s + 1);
  };

  const handleBack = () => {
    setSubmitError('');
    setStep((s) => s - 1);
  };

  const handleSubmit = async () => {
    if (!canAdvance()) {
      setSubmitError('請填完本頁所有欄位再繼續。');
      return;
    }
    setIsSubmitting(true);
    setSubmitError('');

    const payload = {
      topic_id: topicId,
      experiment_condition: condition,
      ...(sessionId ? { session_id: sessionId } : {}),
      ...(roomId ? { room_id: roomId } : {}),
      // C-1
      post_likert_1: c1Answers[1],
      post_likert_2: c1Answers[2],
      post_likert_3: c1Answers[3],
      post_likert_4: c1Answers[4],
      post_likert_5: c1Answers[5],
      post_likert_6: c1Answers[6],
      post_likert_7: c1Answers[7],
      post_likert_8: c1Answers[8],
      // C-2
      ...c2Answers,
      // C-3
      ...c3Answers,
      // C-4
      opponent_judgment: isHH ? null : c4Answer,
      // D
      post_open_comprehension: d1,
      post_open_feedback: d2,
      // E
      discomfort_flag: discomfortFlag,
      discomfort_detail: discomfortDetail,
    };

    try {
      const response = await api.post('/api/post-questionnaire/', payload);
      // Show this dialogue's stance numbers before moving on to debriefing.
      setResult(response.data);
    } catch (error) {
      const detail =
        error?.response?.data?.detail ||
        error?.response?.data?.non_field_errors?.[0] ||
        '送出失敗，請稍後再試。';
      setSubmitError(typeof detail === 'string' ? detail : JSON.stringify(detail));
    } finally {
      setIsSubmitting(false);
    }
  };

  const isLastStep = step === actualSteps - 1;
  const eStep = isHH ? 4 : 5;
  const dStep = isHH ? 3 : 4;

  if (result) {
    return (
      <ResultCard
        data={result}
        onContinue={() =>
          navigate('/debriefing', {
            state: { responseId: result.id },
            replace: true,
          })
        }
      />
    );
  }

  if (!topicId && !sessionId && !roomId) {
    return (
      <div className="pq-page">
        <div className="pq-container">
          <p className="pq-error">找不到對話資訊，請從對話頁面進入問卷。</p>
          <button className="pq-btn pq-btn-primary" onClick={() => navigate('/')}>
            返回首頁
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="pq-page">
      <div className="pq-container">
        <div className="pq-header">
          <h2>對話後問卷</h2>
          <p className="pq-subtitle">感謝你的參與！請依序回答以下問題。</p>
          <div className="pq-step-badge">{getStepLabel()}</div>
          <StepIndicator current={step} total={actualSteps} />
        </div>

        <div className="pq-body">
          {step === 0 && (
            <StepC1
              answers={c1Answers}
              onChange={(idx, v) => setC1Answers((prev) => ({ ...prev, [idx]: v }))}
            />
          )}
          {step === 1 && (
            <StepC2
              answers={c2Answers}
              onChange={(key, v) => setC2Answers((prev) => ({ ...prev, [key]: v }))}
            />
          )}
          {step === 2 && (
            <StepC3
              answers={c3Answers}
              onChange={(key, v) => setC3Answers((prev) => ({ ...prev, [key]: v }))}
            />
          )}
          {!isHH && step === 3 && (
            <StepC4 value={c4Answer} onChange={setC4Answer} />
          )}
          {step === dStep && (
            <StepD
              d1={d1}
              d2={d2}
              onD1Change={setD1}
              onD2Change={setD2}
            />
          )}
          {step === eStep && (
            <StepE
              flag={discomfortFlag}
              detail={discomfortDetail}
              onFlagChange={(v) => { setDiscomfortFlag(v); if (!v) setDiscomfortDetail(''); }}
              onDetailChange={setDiscomfortDetail}
            />
          )}
        </div>

        <div className="pq-footer">
          {submitError && <p className="pq-error">{submitError}</p>}
          <div className="pq-nav-buttons">
            {step > 0 && (
              <button
                className="pq-btn pq-btn-secondary"
                onClick={handleBack}
                disabled={isSubmitting}
              >
                上一步
              </button>
            )}
            {!isLastStep ? (
              <button
                className="pq-btn pq-btn-primary"
                onClick={handleNext}
              >
                下一步
              </button>
            ) : (
              <button
                className="pq-btn pq-btn-submit"
                onClick={handleSubmit}
                disabled={isSubmitting}
              >
                {isSubmitting ? '送出中...' : '送出問卷'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
