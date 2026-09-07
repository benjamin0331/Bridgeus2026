import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import api from '../api/client';
import SettlementReceipt from './SettlementReceipt';
import './PostQuestionnairePage.css';

// --- Static question data -----------------------------------------------

const POST_QUESTION_ORDER = [8, 5, 3, 7, 1, 4, 6, 2];

const C2_QUESTIONS = [
  { key: 'exp_stance_change_1', text: '經過這次對話，我對這個議題的看法有了一些改變。' },
  { key: 'exp_stance_change_2', text: '我現在比對話前更能理解對方立場的合理之處。' },
  { key: 'exp_quality_1', text: '這次對話過程是理性且有建設性的。' },
  { key: 'exp_quality_2', text: '對話中我有感受到被尊重，而非被攻擊。' },
  { key: 'exp_reflection_1', text: '在對話過程中，我曾重新檢視過自己論點的邏輯是否充分。' },
  { key: 'exp_reflection_2', text: '這次對話讓我意識到自己在某些面向的認知可能不夠完整。' },
  { key: 'exp_comprehension_1', text: '經過這次對話，我對這個議題的了解比之前更完整了。' },
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

function StepC1({ answers, onChange, questions }) {
  return (
    <div className="pq-step-content">
      <div className="pq-part-header">
        <h3>Part C-1：立場重測</h3>
        <p>以下題目與先前問卷相同，請依照你目前的想法重新作答（1＝非常不同意，7＝非常同意）。</p>
      </div>
      {questions.map((q) => (
        <LikertItem
          key={q.index}
          label={`C1-${q.index}`}
          
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

function StepD({ d1, d2, questionText, onD1Change, onD2Change }) {
  const d1Length = d1.trim().length;
  const d1Valid = d1Length >= 30;
  return (
    <div className="pq-step-content">
      <div className="pq-part-header">
        <h3>Part D：開放式問題</h3>
      </div>
      <div className="pq-survey-item pq-survey-item-open">
        <h4>D1 | 對立觀點陳述</h4>
        <p>{questionText}</p>
        <textarea
          className={`pq-open-textarea ${!d1Valid && d1.length > 0 ? 'invalid' : ''}`}
          placeholder="請用對方的角度陳述其最有力的論點（最低 30 字）"
          value={d1}
          onChange={(e) => onD1Change(e.target.value)}
          rows={6}
        />
        <div className={`pq-char-count ${d1Valid ? 'ok' : 'warn'}`}>
          {d1Length} 字{d1Valid ? '　✓' : '　（至少需 30 字）'}
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

// DRF 的錯誤有三種形狀：{detail}、{non_field_errors:[...]}、以及欄位層級的
// {欄位名:[訊息]}。原本只讀前兩種，第三種一律變成「送出失敗，請稍後再試」——
// 而後測問卷最容易踩到的偏偏是第三種（找不到對應的 session/房間、開放題字數
// 不足）。受試者看不到真正原因就只能重按，研究者事後也查不出那筆為什麼沒進來。
function extractSubmitError(error) {
  const data = error?.response?.data;

  if (typeof data === 'string' && data.trim()) return data;
  if (typeof data?.detail === 'string') return data.detail;

  const first = (value) => (Array.isArray(value) ? value[0] : value);

  const nonField = first(data?.non_field_errors);
  if (typeof nonField === 'string') return nonField;

  if (data && typeof data === 'object') {
    for (const [field, value] of Object.entries(data)) {
      const message = first(value);
      if (typeof message === 'string' && message.trim()) {
        return `${message}（${field}）`;
      }
    }
  }

  if (error?.response?.status) {
    return `送出失敗（HTTP ${error.response.status}），請稍後再試。`;
  }
  return '送出失敗，請稍後再試。';
}

// 填到一半的答案暫存。演示現場最貴的失敗是「送出失敗／不小心重整／被登出」
// 之後整份重填——受試者通常直接放棄，那個樣本就沒了。
//
// key 綁在「哪一場對話」上，所以同一個人不同場次的草稿不會互相蓋掉；送出成功
// 就清掉，不留殘骸。localStorage 在無痕模式或關閉站台資料時會直接丟例外，
// 所有存取都要包 try/catch——暫存失敗可以接受，擋住填問卷不行。
function postQuestionnaireDraftKey({ condition, sessionId, roomId }) {
  const conversationId = sessionId || roomId;
  return conversationId ? `bridgeus_pq_draft_${condition}:${conversationId}` : null;
}

function readQuestionnaireDraft(key) {
  if (!key) return null;
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
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
  const [topicSurvey, setTopicSurvey] = useState(null);
  const [topicSurveyError, setTopicSurveyError] = useState('');
  const numericTopicId = Number(topicId);
  const hasValidTopicId = Number.isInteger(numericTopicId) && numericTopicId > 0;
  const resolvedTopicSurveyError = hasValidTopicId
    ? topicSurveyError
    : '找不到後測問卷的議題資訊。';

  useEffect(() => {
    let cancelled = false;
    if (!hasValidTopicId) {
      return undefined;
    }

    api.get(`/api/dialogue/topics/${numericTopicId}/survey/`)
      .then((response) => {
        if (!cancelled) setTopicSurvey(response.data);
      })
      .catch(() => {
        if (!cancelled) {
          setTopicSurveyError('無法載入這個議題的後測題目，請稍後再試。');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [hasValidTopicId, numericTopicId]);

  const c1Questions = useMemo(() => {
    const questionsById = new Map(
      (topicSurvey?.questions || []).map((question) => [Number(question.id), question]),
    );
    return POST_QUESTION_ORDER
      .map((preQuestionId, index) => {
        const question = questionsById.get(preQuestionId);
        if (!question) return null;
        return {
          index: index + 1,
          preQ: `Q${preQuestionId}`,
          text: question.text,
          reverse: Boolean(question.reverse_scored),
        };
      })
      .filter(Boolean);
  }, [topicSurvey]);

  const d1QuestionText = useMemo(
    () => (topicSurvey?.open_questions || []).find((question) => question.code === 'Q10')?.text || '',
    [topicSurvey],
  );

  const draftKey = postQuestionnaireDraftKey({ condition, sessionId, roomId });
  // 用 lazy initializer 而不是 useEffect 還原：effect 版本會在還原前先跑一次
  // 儲存 effect，把空白狀態寫回去蓋掉草稿。
  const [draft] = useState(() => readQuestionnaireDraft(draftKey));

  const [step, setStep] = useState(() => {
    const saved = Number(draft?.step);
    if (!Number.isInteger(saved) || saved < 0) return 0;
    // 夾在合法範圍內：H-H 少一步，草稿是舊格式時不能讓 step 落到沒有內容的頁。
    const maxStep = (condition === 'hh' ? 5 : TOTAL_STEPS) - 1;
    return Math.min(saved, maxStep);
  });
  const [c1Answers, setC1Answers] = useState(() => draft?.c1Answers ?? {});
  const [c2Answers, setC2Answers] = useState(() => draft?.c2Answers ?? {});
  const [c3Answers, setC3Answers] = useState(() => draft?.c3Answers ?? {});
  const [c4Answer, setC4Answer] = useState(() => draft?.c4Answer ?? null);
  const [d1, setD1] = useState(() => draft?.d1 ?? '');
  const [d2, setD2] = useState(() => draft?.d2 ?? '');
  const [discomfortFlag, setDiscomfortFlag] = useState(() => Boolean(draft?.discomfortFlag));
  const [discomfortDetail, setDiscomfortDetail] = useState(() => draft?.discomfortDetail ?? '');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [result, setResult] = useState(null);

  // 換頁時回到最上面。.pq-page 是 overflow:hidden，捲動的是卡片本身
  // （.pq-container）與內文（.pq-body）兩層，所以 window.scrollTo 沒用，
  // 兩個容器都要歸零——只歸零其中一個，另一層會留在上一頁的捲動位置。
  const containerRef = useRef(null);
  const bodyRef = useRef(null);

  useEffect(() => {
    containerRef.current?.scrollTo({ top: 0 });
    bodyRef.current?.scrollTo({ top: 0 });
  }, [step]);

  useEffect(() => {
    if (!draftKey) return;
    try {
      localStorage.setItem(
        draftKey,
        JSON.stringify({
          step,
          c1Answers,
          c2Answers,
          c3Answers,
          c4Answer,
          d1,
          d2,
          discomfortFlag,
          discomfortDetail,
        }),
      );
    } catch {
      // 寫不進去就算了，不能因此擋住填問卷。
    }
  }, [
    draftKey,
    step,
    c1Answers,
    c2Answers,
    c3Answers,
    c4Answer,
    d1,
    d2,
    discomfortFlag,
    discomfortDetail,
  ]);

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
    if (step === 0) {
      return c1Questions.length === POST_QUESTION_ORDER.length
        && c1Questions.every((q) => c1Answers[q.index] !== undefined);
    }
    if (step === 1) return C2_QUESTIONS.every((q) => c2Answers[q.key] !== undefined);
    if (step === 2) return C3_QUESTIONS.every((q) => c3Answers[q.key] !== undefined);
    if (!isHH && step === 3) return c4Answer !== null;
    const dStep = isHH ? 3 : 4;
    if (step === dStep) return d1.trim().length >= 30;
    const eStep = isHH ? 4 : 5;
    if (step === eStep) return !discomfortFlag || discomfortDetail.trim().length > 0;
    return true;
  };

  const handleClose = () => {
    const confirmed = window.confirm('確定要離開問卷嗎？已填的內容會暫存，回來可以接著填。');
    if (confirmed) {
      navigate(-1);
    }
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
      if (draftKey) {
        try {
          localStorage.removeItem(draftKey);
        } catch {
          // 清不掉不影響已經送出的問卷；下次同一場也不會再被還原（key 綁對話）。
        }
      }
      setResult(response.data);
    } catch (error) {
      setSubmitError(extractSubmitError(error));
    } finally {
      setIsSubmitting(false);
    }
  };

  const isLastStep = step === actualSteps - 1;
  const eStep = isHH ? 4 : 5;
  const dStep = isHH ? 3 : 4;

  if (result) {
    return (
      <SettlementReceipt
        data={result}
        sessionId={sessionId}
        roomId={roomId}
        condition={condition}
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

  if (resolvedTopicSurveyError || !topicSurvey) {
    return (
      <div className="pq-page">
        <div className="pq-container">
          <p className={resolvedTopicSurveyError ? 'pq-error' : 'pq-subtitle'}>
            {resolvedTopicSurveyError || '正在載入後測問卷...'}
          </p>
          {resolvedTopicSurveyError && (
            <button className="pq-btn pq-btn-primary" onClick={() => navigate(-1)}>
              返回對話
            </button>
          )}
        </div>
      </div>
    );
  }

  const closeButton = (
    <button
      type="button"
      className="pq-close-btn"
      onClick={handleClose}
      aria-label="離開問卷並返回對話"
      title="離開問卷並返回對話"
    >
      ×
    </button>
  );

  return (
    <div className="pq-page">
      <div className="pq-container" ref={containerRef}>
        {/* 頁首只在第一頁完整顯示：標題與作答指示看過一次就夠了，之後每一頁都
            留著等於固定吃掉兩列高度。第二頁之後只剩徽章、進度點與離開鈕，
            關閉鈕跟著併進進度列，不另外佔一行。 */}
        <div className="pq-header">
          {step === 0 && (
            <div className="pq-header-top">
              <div className="pq-title-line">
                <h2>對話後問卷</h2>
                <p className="pq-subtitle">感謝你的參與！請依序回答以下問題。</p>
              </div>
              {closeButton}
            </div>
          )}
          <div className="pq-progress-row">
            <div className="pq-step-badge">{getStepLabel()}</div>
            <StepIndicator current={step} total={actualSteps} />
            {step > 0 && closeButton}
          </div>
        </div>

        <div className="pq-body" ref={bodyRef}>
          {step === 0 && (
            <StepC1
              answers={c1Answers}
              questions={c1Questions}
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
              questionText={d1QuestionText}
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
