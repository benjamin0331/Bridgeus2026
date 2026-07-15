import { useNavigate } from 'react-router-dom';
import './StanceReuseModal.css';

function formatStanceCategory(category) {
  if (category === 'support') {
    return '較支持本議題';
  }
  if (category === 'oppose') {
    return '較反對本議題';
  }
  if (category === 'neutral') {
    return '立場中立或尚未明確';
  }
  return '先前已建立立場';
}

function formatUpdatedAt(value) {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  return new Intl.DateTimeFormat('zh-TW', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function StanceReuseModal({
  isOpen,
  stanceScore = null,
  stanceCategory = null,
  updatedAt = null,
  isBusy = false,
  onRedo,
  onReuse,
}) {
  const navigate = useNavigate();

  if (!isOpen) {
    return null;
  }

  const hasScore =
    stanceScore !== null && stanceScore !== undefined && stanceScore !== '';
  const numericScore = Number(stanceScore);
  const scoreDisplay = Number.isFinite(numericScore)
    ? numericScore.toFixed(2)
    : stanceScore;
  const updatedDisplay = formatUpdatedAt(updatedAt);

  return (
    <div className="modal-overlay">
      <div className="modal-content stance-reuse-content">
        <div className="modal-close-btn" onClick={() => navigate('/')}>✕</div>

        <div className="stance-reuse-header">
          <span className="stance-reuse-kicker">已完成前側問卷</span>
          <h2>要重新填寫前側問卷嗎？</h2>
          <p>
            你先前已經填過這個議題的前側問卷。你可以重新填寫以重新計算立場，
            或直接沿用先前的問卷立場開始新的對話。
          </p>
        </div>

        {(hasScore || stanceCategory || updatedDisplay) && (
          <div className="stance-reuse-meta">
            {hasScore && (
              <div className="stance-reuse-row">
                <span className="stance-reuse-label">先前立場分數</span>
                <span className="stance-reuse-value">{scoreDisplay}</span>
              </div>
            )}
            {stanceCategory && (
              <div className="stance-reuse-row">
                <span className="stance-reuse-label">先前立場類型</span>
                <span className="stance-reuse-value">
                  {formatStanceCategory(stanceCategory)}
                </span>
              </div>
            )}
            {updatedDisplay && (
              <div className="stance-reuse-row">
                <span className="stance-reuse-label">上次填寫時間</span>
                <span className="stance-reuse-value">{updatedDisplay}</span>
              </div>
            )}
          </div>
        )}

        <div className="stance-reuse-actions">
          <button
            className="stance-reuse-btn primary"
            type="button"
            onClick={onRedo}
            disabled={isBusy}
          >
            重新填寫問卷
          </button>
          <button
            className="stance-reuse-btn secondary"
            type="button"
            onClick={onReuse}
            disabled={isBusy}
          >
            {isBusy ? '處理中...' : '沿用先前立場'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default StanceReuseModal;
