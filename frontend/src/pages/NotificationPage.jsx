import { useNavigate } from 'react-router-dom';
import { useNotifications } from '../context/NotificationsContext';
import './NotificationPage.css';

function formatNotificationTime(value) {
  if (!value) {
    return '時間未知';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function truncatePreview(text, maxLength = 40) {
  const cleaned = String(text || '').replace(/\s+/g, ' ').trim();
  if (!cleaned) {
    return '（對方尚未回覆）';
  }
  return cleaned.length <= maxLength ? cleaned : `${cleaned.slice(0, maxLength)}…`;
}

function NotificationPage() {
  const navigate = useNavigate();
  const { rooms, markRoomRead } = useNotifications();

  const handleOpenRoom = (room) => {
    markRoomRead(room.room_id, room.message_count);
    if (room.status === 'active') {
      navigate(`/topic/${room.topic_id}?mode=${room.kind === 'ai' ? 'ai' : 'match'}`);
      return;
    }
    // 已結束／已取消的對話回不去聊天室了，改跳去歷史紀錄直接開啟這筆對話，
    // 並自動切到真人對話／AI 對話對應的分頁。
    navigate('/history', { state: { kind: room.kind, id: room.room_id } });
  };

  return (
    <div className="notification-page">
      <div className="notification-heading">
        <span className="notification-kicker">Notifications</span>
        <h1>消息通知</h1>
        <p>查看聊天室的新訊息，點擊即可跳轉回該聊天室。</p>
        <p>若聊天室已結束或已取消，將會跳轉到歷史紀錄頁面。</p>
      </div>

      <div className="notification-list">
        {rooms.length === 0 && (
          <div className="notification-empty-card">目前還沒有任何聊天紀錄。</div>
        )}

        {rooms.map((room) => {
          const unread = room.unreadCount > 0;
          return (
            <button
              key={`${room.kind}-${room.room_id}`}
              type="button"
              className={`notification-card${unread ? ' is-unread' : ''}`}
              onClick={() => handleOpenRoom(room)}
            >
              <div className="notification-card-main">
                <strong>{room.topic_title || `議題 ${room.topic_id}`}</strong>
                <span className="notification-preview">
                  {unread ? '有新訊息：' : ''}
                  {truncatePreview(room.last_partner_message_preview)}
                </span>
              </div>
              <div className="notification-card-meta-col">
                {unread && (
                  <span className="notification-dot" aria-label={`${room.unreadCount} 則未讀訊息`}>
                    {room.unreadCount}
                  </span>
                )}
                <span className="notification-card-meta">
                  {formatNotificationTime(room.last_activity_at)}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default NotificationPage;
