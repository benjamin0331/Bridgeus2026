import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import api from '../api/client';
import './PostQuestionnairePage.css';
import './PlatformFeedbackPage.css';

// --- Static question data (Part F) ---------------------------------------

// F1–F5：功能體驗（7 點量表，1＝非常不滿意，7＝非常滿意）
const F_LIKERT_QUESTIONS = [
  { key: 'ux_matching', label: 'F1', text: '問卷填寫與配對等待的流程', tag: '配對機制' },
  { key: 'ux_chatroom', label: 'F2', text: '對話室的操作與訊息傳遞', tag: '對話室體驗' },
  { key: 'ux_nlp_intervention', label: 'F3', text: '系統提示（如情緒提醒、離題引導）', tag: 'NLP 介入' },
  { key: 'ux_ccnd', label: 'F4', text: '概念認知網路圖（右側思維導圖）', tag: 'CCND' },
  { key: 'ux_overall', label: 'F5', text: '整體平台的操作流暢度', tag: '系統可用性' },
];

const SATISFACTION_VALUES = [1, 2, 3, 4, 5, 6, 7];
const NPS_VALUES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

// --- Sub-components -------------------------------------------------------

function SatisfactionItem({ label, text, tag, value, onChange }) {
  return (
    <div className="pq-survey-item">
      <h4>{label}{tag ? <span className="pq-tag">{tag}</span> : null}</h4>
      <p>{text}</p>
      <div className="pq-likert-scale">
        <div className="pq-likert-line" />
        {SATISFACTION_VALUES.map((v) => (
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
        <span>非常不滿意</span>
        <span>非常滿意</span>
      </div>
    </div>
  );
}

function NpsItem({ value, onChange }) {
  return (
    <div className="pq-survey-item">
      <h4>F6<span className="pq-tag">推薦意願</span></h4>
      <p>你有多大可能會推薦這個平台給對公共議題有興趣的朋友？</p>
      <div className="pf-nps-scale">
        {NPS_VALUES.map((v) => (
          <button
            key={v}
            type="button"
            className={`pf-nps-dot ${value === v ? 'selected' : ''}`}
            onClick={() => onChange(v)}
            aria-pressed={value === v}
          >
            {v}
          </button>
        ))}
      </div>
      <div className="pq-likert-labels">
        <span>完全不可能</span>
        <span>非常可能</span>
      </div>
    </div>
  );
}

// --- Main page ------------------------------------------------------------

export default function PlatformFeedbackPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { responseId, withdrawn = false } = location.state || {};

  const [likert, setLikert] = useState({});
  const [nps, setNps] = useState(null);
  const [improvement, setImprovement] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [done, setDone] = useState(false);

  const canSubmit =
    F_LIKERT_QUESTIONS.every((q) => likert[q.key] !== undefined) && nps !== null;

  const handleSubmit = async () => {
    if (!canSubmit) {
      setSubmitError('請完成 F1–F6 的評分再送出。');
      return;
    }
    if (!responseId) {
      setSubmitError('找不到問卷紀錄，請聯絡研究團隊。');
      return;
    }
    setIsSubmitting(true);
    setSubmitError('');

    const payload = {
      response_id: responseId,
      ux_matching: likert.ux_matching,
      ux_chatroom: likert.ux_chatroom,
      ux_nlp_intervention: likert.ux_nlp_intervention,
      ux_ccnd: likert.ux_ccnd,
      ux_overall: likert.ux_overall,
      nps_score: nps,
      ux_improvement: improvement,
    };

    try {
      await api.post('/api/platform-feedback/', payload);
      setDone(true);
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

  if (!responseId && !done) {
    return (
      <div className="pq-page">
        <div className="pq-container">
          <p className="pq-error">找不到問卷紀錄，請從問卷流程進入本頁。</p>
          <button className="pq-btn pq-btn-primary" onClick={() => navigate('/')}>
            返回首頁
          </button>
        </div>
      </div>
    );
  }

  if (done) {
    return (
      <div className="pq-page">
        <div className="pq-container">
          <div className="pf-thank-you">
            <h2>感謝你的參與！</h2>
            {withdrawn ? (
              <p className="pf-withdrawn-note">
                你的資料已標記為撤回，不會被納入研究分析。
                <br />
                感謝你投入時間參與這次實驗。
              </p>
            ) : (
              <p>你的回饋已完整記錄，這將幫助我們把系統做得更好。</p>
            )}
            <button
              className="pq-btn pq-btn-primary"
              onClick={() => navigate('/', { replace: true })}
            >
              返回首頁
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="pq-page">
      <div className="pq-container">
        <div className="pq-header">
          <h2>平台體驗回饋</h2>
          <p className="pq-subtitle">
            最後想請你花 2 分鐘，幫我們回饋一下平台的使用體驗，這會幫助我們把系統做得更好。
          </p>
          <div className="pq-step-badge">最後幾個小問題</div>
        </div>

        <div className="pq-body">
          <div className="pq-step-content">
            <div className="pq-part-header">
              <h3>功能體驗</h3>
              <p>請對以下平台功能的使用體驗給予評分（1＝非常不滿意，7＝非常滿意）。</p>
            </div>
            {F_LIKERT_QUESTIONS.map((q) => (
              <SatisfactionItem
                key={q.key}
                label={q.label}
                tag={q.tag}
                text={q.text}
                value={likert[q.key]}
                onChange={(v) => setLikert((prev) => ({ ...prev, [q.key]: v }))}
              />
            ))}

            <NpsItem value={nps} onChange={setNps} />

            <div className="pq-survey-item pq-survey-item-open">
              <h4>F7<span className="pq-tag">選填</span></h4>
              <p>你覺得這個平台最需要改進的地方是什麼？（選填，可跳過）</p>
              <textarea
                className="pq-open-textarea"
                placeholder="自由填寫，可跳過"
                value={improvement}
                onChange={(e) => setImprovement(e.target.value)}
                rows={4}
              />
            </div>
          </div>
        </div>

        <div className="pq-footer">
          {submitError && <p className="pq-error">{submitError}</p>}
          <div className="pq-nav-buttons">
            <button
              className="pq-btn pq-btn-submit"
              onClick={handleSubmit}
              disabled={isSubmitting}
            >
              {isSubmitting ? '送出中...' : '送出回饋'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
