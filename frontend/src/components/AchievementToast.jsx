import './AchievementToast.css';

// achievement: { code, name, description, title? }，來自 GET /api/achievements/me/
// 的 newly_unlocked；null 時不顯示
function AchievementToast({ achievement, onClose, onOpen }) {
  if (!achievement) return null;

  return (
    <button type="button" className="achievement-toast" onClick={onOpen}>
      <div className="achievement-toast-body">
        <span className="achievement-toast-kicker">成就解鎖</span>
        <span className="achievement-toast-name">{achievement.name}</span>
        {achievement.title && (
          <span className="achievement-toast-title">獲得頭銜「{achievement.title}」</span>
        )}
        <span className="achievement-toast-desc">{achievement.description}</span>
      </div>
      <span
        className="achievement-toast-close"
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

export default AchievementToast;
