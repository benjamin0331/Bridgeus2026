import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './SurveyModal.css';

function SurveyModal({ isOpen, onSubmit }) {
  const navigate = useNavigate();
  const [selectedAnswers, setSelectedAnswers] = useState({});

  // 若 isOpen 為 false，則不渲染此元件
  if (!isOpen) return null;

  // 問卷題目設定
  const dynamicQuestions = [
    { id: 1, tag: "整體態度", text: "整體而言，我認同目前對此議題的政策／主張方向" },
    { id: 2, tag: "必要性評估", text: "我認為這個議題被提出來，是有其必要性與現實考量的" },
    { id: 3, tag: "影響評估", text: "我認為此議題相關的措施，整體來說利大於弊" },
    { id: 4, tag: "價值認同", text: "即使這項措施對我個人沒有直接好處，我仍能接受它" },
    { id: 5, tag: "推動意願", text: "如果有機會，我會支持這類政策／主張持續推動或被討論" }
  ];

  // 處理李克特量表 (Likert Scale) 的點擊事件
  const handleDotClick = (questionId, value) => {
    setSelectedAnswers(prev => ({ ...prev, [questionId]: value }));
  };

  // 提交前的檢查邏輯
  const handleFinalSubmit = () => {
    // 檢查是否所有題目皆已填寫 (本問卷固定 5 題)
    if (Object.keys(selectedAnswers).length === 5) {
      onSubmit({ answers: selectedAnswers }); // 呼叫父元件傳入的提交處理函式
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
          <h2>立場檢測問卷</h2>
          <p>為瞭解您的立場以便於快速幫您尋找配對用戶...</p>
        </div>

        {/* 問卷題目區域 */}
        <div className="survey-grid">
          {dynamicQuestions.map((q) => (
            <div key={q.id} className="survey-item">
              <h4>題目 {q.id} | {q.tag}</h4>
              <p>{q.text}</p>
              
              {/* 五階李克特量表設計 */}
              <div className="likert-scale">
                <div className="likert-line"></div>
                {[1, 2, 3, 4, 5].map(v => (
                  <div 
                    key={v} 
                    className={`likert-dot ${selectedAnswers[q.id] === v ? 'selected' : ''}`} 
                    onClick={() => handleDotClick(q.id, v)} 
                  />
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* 提交按鈕區域 */}
        <div className="survey-footer">
          <button 
            className="submit-survey-btn ready" 
            onClick={handleFinalSubmit}
          >
            填寫完畢
          </button>
        </div>
      </div>
    </div>
  );
}

export default SurveyModal;
