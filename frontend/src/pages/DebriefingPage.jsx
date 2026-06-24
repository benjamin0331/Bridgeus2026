import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import api from '../api/client';
import './DebriefingPage.css';

export default function DebriefingPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { responseId } = location.state || {};

  const [choice, setChoice] = useState(null); // 'agree' | 'withdraw'
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);
  const [withdrawnConfirmed, setWithdrawnConfirmed] = useState(false);

  const handleConfirm = async () => {
    if (!choice) {
      setError('請選擇一個選項。');
      return;
    }
    if (!responseId) {
      setError('找不到問卷紀錄，請聯絡研究團隊。');
      return;
    }

    setIsSubmitting(true);
    setError('');

    try {
      const consentConfirmed = choice === 'agree';
      await api.patch(`/api/post-questionnaire/${responseId}/consent/`, {
        consent_confirmed: consentConfirmed,
      });
      setWithdrawnConfirmed(!consentConfirmed);
      setDone(true);
    } catch (err) {
      const detail =
        err?.response?.data?.detail ||
        '送出失敗，請稍後再試或聯絡研究團隊。';
      setError(detail);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (done) {
    return (
      <div className="db-page">
        <div className="db-container">
          <div className="db-thank-you">
            <h2>感謝你的參與！</h2>
            {withdrawnConfirmed ? (
              <p className="db-withdrawn-note">
                你的資料已標記為撤回，不會被納入研究分析。
                <br />
                感謝你投入時間參與這次實驗。
              </p>
            ) : (
              <p>
                你的回答已完整記錄。你的貢獻對這份研究非常重要。
              </p>
            )}
            <button
              className="db-btn db-btn-primary"
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
    <div className="db-page">
      <div className="db-container">
        <div className="db-content">
          <h2>感謝你的參與！</h2>

          <div className="db-section">
            <p>
              在開始說明之前，我們想先感謝你投入時間參與這次實驗。你的每一次回答與對話，
              都是這個研究能否成功的關鍵。
            </p>
          </div>

          <div className="db-section">
            <h3>關於你的對話對象</h3>
            <p>
              在這次實驗中，部分參與者的對話對象是由 AI 系統所扮演的。如果你的對話對象是 AI，
              這並不代表你的對話內容沒有價值——事實上，你與 AI 的互動方式正是我們研究的核心之一。
            </p>
            <p>
              我們的研究目的是比較「人與人」和「人與 AI」兩種對話模式在促進觀點理解方面的差異，
              藉此探索 AI 是否能作為促進社會溝通的工具。
            </p>
          </div>

          <div className="db-section">
            <h3>關於你的資料</h3>
            <p>
              你的所有回答與對話紀錄將以匿名方式處理，僅用於本學術研究。
              任何公開的研究成果中，都不會出現能識別你個人身份的資訊。
            </p>
          </div>

          <div className="db-consent-section">
            <p className="db-consent-prompt">請選擇你的資料使用意願：</p>
            <div className="db-radio-group">
              <label className="db-radio-label">
                <input
                  type="radio"
                  name="consent"
                  value="agree"
                  checked={choice === 'agree'}
                  onChange={() => setChoice('agree')}
                />
                <span>
                  我已閱讀以上說明，同意我的資料繼續用於本研究。
                </span>
              </label>
              <label className="db-radio-label">
                <input
                  type="radio"
                  name="consent"
                  value="withdraw"
                  checked={choice === 'withdraw'}
                  onChange={() => setChoice('withdraw')}
                />
                <span>
                  我希望撤回我的資料，不再用於本研究。
                </span>
              </label>
            </div>
          </div>

          {error && <p className="db-error">{error}</p>}

          <div className="db-footer">
            <button
              className="db-btn db-btn-primary"
              onClick={handleConfirm}
              disabled={isSubmitting || !choice}
            >
              {isSubmitting ? '確認中...' : '確認'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
