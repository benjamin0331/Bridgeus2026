import React, { useState } from 'react';

function Sidebar({ navigate, isTopicPage = false }) {
  const [isOpen, setIsOpen] = useState(false);
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
          <img src="/bell.png" alt="Notification" className="utility-icon" />
          <img src="/star.png" alt="Favorite" className="utility-icon" />
          <button
            className="sidebar-icon-btn"
            type="button"
            onClick={() => handleNavigate('/history')}
            aria-label="歷史對話"
            title="歷史對話"
          >
            <img src="/history.svg" alt="" className="utility-icon" />
          </button>
          <img src="/settings.png" alt="Settings" className="utility-icon" />
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
