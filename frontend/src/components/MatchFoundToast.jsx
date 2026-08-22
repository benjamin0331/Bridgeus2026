import './MatchFoundToast.css';

// alert: { topicId, roomId, matchedAt }，由 MatchingHeartbeatContext 偵測到
// 配對成功時產生；null 時不顯示。
//
// ⚠️ 外層是 div[role=button] 而不是真的 <button>：關閉的 × 本身也要是可聚焦的
// 按鈕，而 HTML 不允許 button 裡面再包 button（巢狀互動元素），瀏覽器會把內層
// 拆出去，鍵盤使用者也 tab 不到它——原本 × 是 span[role=button] 沒有 tabIndex，
// 等於只有滑鼠點得到。div 沒有原生的 Enter/Space 行為，所以要自己補 onKeyDown。
// AchievementToast 是同一套結構，兩邊要一起改。
function MatchFoundToast({ alert, stacked, onClose, onOpen }) {
  if (!alert) return null;

  return (
    <div
      role="button"
      tabIndex={0}
      className={`match-found-toast${stacked ? ' match-found-toast--stacked' : ''}`}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();   // Space 預設會捲動頁面
          onOpen();
        }
      }}
    >
      <div className="match-found-toast-body">
        <span className="match-found-toast-kicker">配對成功</span>
        <span className="match-found-toast-name">已為你找到對話夥伴</span>
        <span className="match-found-toast-desc">點擊即可進入對話室</span>
      </div>
      <button
        type="button"
        className="match-found-toast-close"
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

export default MatchFoundToast;
