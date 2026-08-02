/* eslint-disable react-hooks/set-state-in-effect */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './SurveyModal.css';

function SurveyModal({
  isOpen,
  survey,
  isLoading,
  error,
  isSubmitting = false,
  submitError = '',
  onSubmit,
  deadline = null,
}) {
  const navigate = useNavigate();
  const [selectedAnswers, setSelectedAnswers] = useState({});
  const [openAnswers, setOpenAnswers] = useState({});

  // 只有 Godot 強制綁定的問卷有期限；一般入口不傳這個 prop，不顯示倒數。
  // 歸零時前端不自己判定作廢——單一真值來源在後端（階段五的裁決），兩邊時鐘
  // 不一致的話會出現「一方以為還有時間、一方已經作廢」。
  const [remainingMs, setRemainingMs] = useState(null);

  useEffect(() => {
    if (!deadline) {
      setRemainingMs(null);
      return undefined;
    }
    const target = new Date(deadline).getTime();
    if (Number.isNaN(target)) {
      setRemainingMs(null);
      return undefined;
    }
    const tick = () => setRemainingMs(Math.max(0, target - Date.now()));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [deadline]);

  const surveyTitle = survey?.title || '立場檢測問卷';
  const surveySubtitle = survey?.subtitle || '正在整理問卷內容...';
  const questions = survey?.questions || [];
  const openQuestions = survey?.open_questions || [];
  const scaleMin = survey?.scale?.min ?? 1;
  const scaleMax = survey?.scale?.max ?? 7;
  const scaleValues = Array.from(
    { length: scaleMax - scaleMin + 1 },
    (_, index) => scaleMin + index,
  );
  const minLabel = survey?.scale?.min_label || '';
  const maxLabel = survey?.scale?.max_label || '';

  useEffect(() => {
    setSelectedAnswers({});
    setOpenAnswers({});
  }, [survey?.topic_id]);

  if (!isOpen) return null;

  const handleDotClick = (questionId, value) => {
    setSelectedAnswers((prev) => ({ ...prev, [questionId]: value }));
  };

  const handleOpenAnswerChange = (questionCode, value) => {
    setOpenAnswers((prev) => ({ ...prev, [questionCode]: value }));
  };

  const handleFinalSubmit = () => {
    const answeredLikertCount = questions.filter(
      (q) => selectedAnswers[q.id] !== undefined,
    ).length;
    const answeredOpenCount = openQuestions.filter(
      (q) => (openAnswers[q.code] || '').trim().length > 0,
    ).length;

    if (
      questions.length > 0 &&
      answeredLikertCount === questions.length &&
      answeredOpenCount === openQuestions.length
    ) {
      onSubmit({
        answers: selectedAnswers,
        openAnswers,
      });
    } else {
      alert('請填完所有問題再提交！');
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <div className="modal-close-btn" onClick={() => navigate('/')}>✕</div>

        <div className="survey-header">
          <h2>{surveyTitle}</h2>
          <p>{surveySubtitle}</p>
          {remainingMs !== null && (
            <span className={remainingMs <= 60000 ? 'survey-countdown survey-countdown--urgent' : 'survey-countdown'}>
              剩餘 {String(Math.floor(remainingMs / 60000)).padStart(2, '0')}:
              {String(Math.floor((remainingMs % 60000) / 1000)).padStart(2, '0')}
            </span>
          )}
        </div>

        <div className="survey-grid">
          {isLoading && <div className="survey-status">正在載入問卷...</div>}
          {!isLoading && error && <div className="survey-status survey-error">{error}</div>}
          {!isLoading && !error && questions.map((q) => (
            <div key={q.id} className="survey-item">
              <h4>題目 {q.id} | {q.tag}</h4>
              <p>{q.text}</p>

              <div className="likert-scale">
                <div className="likert-line"></div>
                {scaleValues.map((v) => (
                  <div
                    key={v}
                    className={`likert-dot ${selectedAnswers[q.id] === v ? 'selected' : ''}`}
                    onClick={() => handleDotClick(q.id, v)}
                  />
                ))}
              </div>
              {(minLabel || maxLabel) && (
                <div className="likert-labels">
                  <span>{minLabel}</span>
                  <span>{maxLabel}</span>
                </div>
              )}
            </div>
          ))}

          {!isLoading && !error && openQuestions.map((q) => (
            <div key={q.code || q.id} className="survey-item survey-item-open">
              <h4>題目 {q.id} | {q.tag}</h4>
              <p>{q.text}</p>
              <textarea
                className="survey-open-textarea"
                placeholder={q.placeholder || '請輸入你的回答'}
                value={openAnswers[q.code] || ''}
                onChange={(event) => handleOpenAnswerChange(q.code, event.target.value)}
                rows={5}
              />
              {(q.min_sentences || q.max_sentences) && (
                <div className="survey-open-hint">
                  建議作答 {q.min_sentences || '?'}–{q.max_sentences || '?'} 句
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="survey-footer">
          {submitError && (
            <p className="survey-submit-error">{submitError}</p>
          )}
          <button
            className="submit-survey-btn ready"
            onClick={handleFinalSubmit}
            disabled={isLoading || isSubmitting || Boolean(error) || questions.length === 0}
          >
            {isSubmitting ? '正在加入匹配...' : '填寫完畢'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default SurveyModal;
