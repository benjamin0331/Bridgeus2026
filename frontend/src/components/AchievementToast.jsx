import './AchievementToast.css';

// achievement: { code, name, description, title? }，來自 GET /api/achievements/me/
// 的 newly_unlocked；null 時不顯示
//
// ⚠️ 外層是 div[role=button] 而不是真的 <button>：理由見 MatchFoundToast.jsx
// 的同一段註解（HTML 不允許 button 巢狀，關閉的 × 才能是可聚焦的按鈕）。
// 兩個 toast 是同一套結構，要改就一起改。
function AchievementToast({ achievement, onClose, onOpen }) {
  if (!achievement) return null;

  return (
    <div
      role="button"
      tabIndex={0}
      className="achievement-toast"
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();   // Space 預設會捲動頁面
          onOpen();
        }
      }}
    >
      <div className="achievement-toast-body">
        <span className="achievement-toast-kicker">成就解鎖</span>
        <span className="achievement-toast-name">{achievement.name}</span>
        {achievement.title && (
          <span className="achievement-toast-title">獲得頭銜「{achievement.title}」</span>
        )}
        <span className="achievement-toast-desc">{achievement.description}</span>
      </div>
      <button
        type="button"
        className="achievement-toast-close"
        aria-label="關閉"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
      >
        ×
      </button>
    </div>
  );
}

export default AchievementToast;
