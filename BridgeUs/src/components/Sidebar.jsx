import React from 'react';

function Sidebar({ navigate }) {
  return (
    <aside className="sidebar-container">
      {/* 使用者大頭貼顯示區域 */}
      <div className="avatar-frame">
        <img src="/icon.jpg" alt="User Avatar" className="avatar-image" />
      </div>

      {/* 側邊欄功能選單：包含通知、收藏與設定 */}
      <div className="sidebar-menu-icons">
        <img src="/bell.png" alt="Notification" className="utility-icon" />
        <img src="/star.png" alt="Favorites" className="utility-icon" />
        <img src="/settings.png" alt="Settings" className="utility-icon" />
      </div>

      {/* 回到首頁導航按鈕：點擊後執行 navigate('/') 跳轉 */}
      <div className="home-navigation-btn" onClick={() => navigate('/')}>
        <img src="/home.png" alt="Go Home" className="utility-icon" />
      </div>
    </aside>
  );
}

export default Sidebar;