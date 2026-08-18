import './MatchFoundToast.css';

// alert: { topicId, roomId, matchedAt }，由 MatchingHeartbeatContext 偵測到
// 配對成功時產生；null 時不顯示。
function MatchFoundToast({ alert, stacked, onClose, onOpen }) {
  if (!alert) return null;

  return (
    <button
      type="button"
      className={`match-found-toast${stacked ? ' match-found-toast--stacked' : ''}`}
      onClick={onOpen}
    >
      <div className="match-found-toast-body">
        <span className="match-found-toast-kicker">配對成功</span>
        <span className="match-found-toast-name">已為你找到對話夥伴</span>
        <span className="match-found-toast-desc">點擊即可進入對話室</span>
      </div>
      <span
        className="match-found-toast-close"
        role="button"
        aria-label="關閉"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
      >
        ×
      </span>
    </button>
  );
}

export default MatchFoundToast;
