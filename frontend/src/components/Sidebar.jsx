import React, { useState } from 'react';
import { useNotifications } from '../context/NotificationsContext';

function Sidebar({ navigate, isTopicPage = false, isResearcher = false }) {
  const [isOpen, setIsOpen] = useState(false);
  const { hasUnread } = useNotifications();
  const handleNavigate = (path) => {
    navigate(path);
    setIsOpen(false);
  };

  return (
    <>
      {/* 手機版：點擊遠罩關閉側邊欄 */}
      {isOpen && (
        <div className="sidebar-backdrop" onClick={() => setIsOpen(false)} />
      )}

      {/* 手機版：側邊欄展開按鈕（桑機隱藏）*/}
      {!isOpen && (
        <button
          className={`sidebar-toggle-btn${isTopicPage ? ' sidebar-toggle-btn--chat' : ''}`}
          onClick={() => setIsOpen(true)}
          aria-label="展開側邊欄"
        >
          ☰
        </button>
      )}

      <aside className={`sidebar-container${isOpen ? ' sidebar-open' : ''}`}>
        {/* 使用者大頭貼顯示區域 */}
        <div className="avatar-frame">
          <img src="/icon.jpg" alt="User Avatar" className="avatar-image" />
        </div>

        {/* 側邊欄功能選單：包含通知、收藏、歷史對話與設定 */}
        <div className="sidebar-menu-icons">
          <button
            className="sidebar-icon-btn"
            type="button"
            onClick={() => handleNavigate('/achievement')}
            aria-label="成就"
            title="成就"
          >
            <img src="/Achievement.png" alt="" className="utility-icon" />
          </button>
          <button
            className="sidebar-icon-btn"
            type="button"
            onClick={() => handleNavigate('/kb')}
            aria-label="觀點知識庫"
            title="觀點知識庫"
          >
            <img src="/star.png" alt="" className="utility-icon" />
          </button>
          <button
            className="sidebar-icon-btn"
            type="button"
            onClick={() => handleNavigate('/notifications')}
            aria-label="消息通知"
            title="消息通知"
          >
            <img src="/bell.png" alt="" className="utility-icon" />
            {hasUnread && <span className="sidebar-notification-dot" aria-hidden="true" />}
          </button>
          <button
            className="sidebar-icon-btn"
            type="button"
            onClick={() => handleNavigate('/history')}
            aria-label="歷史對話"
            title="歷史對話"
          >
            <img src="/history.svg" alt="" className="utility-icon" />
          </button>
          <button
            className="sidebar-icon-btn"
            type="button"
            onClick={() => handleNavigate('/settings')}
            aria-label="設定"
            title="設定"
          >
            <img src="/settings.png" alt="" className="utility-icon" />
          </button>
          {/* 只有屬於「研究者」Group 的帳號才看得到，實際存取控制在後端
              IsResearcher，這裡只是決定要不要顯示連結。 */}
          {isResearcher && (
            <button
              className="sidebar-icon-btn"
              type="button"
              onClick={() => handleNavigate('/viewpoint-review')}
              aria-label="觀點知識庫審核"
              title="觀點知識庫審核"
            >
              📋
            </button>
          )}
        </div>

        {/* 回到首頁導航按鈕：點擊後執行 navigate('/') 跳轉 */}
        <div className="home-navigation-btn" onClick={() => handleNavigate('/')}>
          <img src="/home.png" alt="Go Home" className="utility-icon" />
        </div>
      </aside>
    </>
  );
}

export default Sidebar;
