import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './SurveyModal.css';

function SurveyModal({ isOpen, survey, isLoading, error, onSubmit }) {
  const navigate = useNavigate();
  const [selectedAnswers, setSelectedAnswers] = useState({});
  const [openAnswers, setOpenAnswers] = useState({});

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

  // 若 isOpen 為 false，則不渲染此元件
  if (!isOpen) return null;

  // 處理李克特量表 (Likert Scale) 的點擊事件
  const handleDotClick = (questionId, value) => {
    setSelectedAnswers(prev => ({ ...prev, [questionId]: value }));
  };

  const handleOpenAnswerChange = (questionCode, value) => {
    setOpenAnswers(prev => ({ ...prev, [questionCode]: value }));
  };

  // 提交前的檢查邏輯
  const handleFinalSubmit = () => {
    const answeredLikertCount = questions.filter(
      q => selectedAnswers[q.id] !== undefined,
    ).length;
    const answeredOpenCount = openQuestions.filter(
      q => (openAnswers[q.code] || '').trim().length > 0,
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
      alert("請填完所有問題再提交！");
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        {/* 關閉按鈕：點擊後導向首頁，視同放棄進入聊天室 */}
        <div className="modal-close-btn" onClick={() => navigate('/')}>✕</div>
        
        <div className="survey-header">
          <h2>{surveyTitle}</h2>
          <p>{surveySubtitle}</p>
        </div>

        {/* 問卷題目區域 */}
        <div className="survey-grid">
          {isLoading && <div className="survey-status">正在載入問卷...</div>}
          {!isLoading && error && <div className="survey-status survey-error">{error}</div>}
          {!isLoading && !error && questions.map((q) => (
            <div key={q.id} className="survey-item">
              <h4>題目 {q.id} | {q.tag}</h4>
              <p>{q.text}</p>

              {/* 李克特量表設計 */}
              <div className="likert-scale">
                <div className="likert-line"></div>
                {scaleValues.map(v => (
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

        {/* 提交按鈕區域 */}
        <div className="survey-footer">
          <button
            className="submit-survey-btn ready"
            onClick={handleFinalSubmit}
            disabled={isLoading || Boolean(error) || questions.length === 0}
          >
            填寫完畢
          </button>
        </div>
      </div>
    </div>
  );
}

export default SurveyModal;
