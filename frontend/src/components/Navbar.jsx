function Navbar({ navigate, userName, onLogout, hideLogout = false }) {
  return (
    <nav className="top-navigation">
      <div className="brand-logo" onClick={() => navigate('/')} style={{ cursor: 'pointer' }}>
        TakeAbridge
      </div>
      <div className="top-navigation-actions">
        <div className="user-profile-name">{userName}</div>
        {/* 對話頁不給登出。可以直接不渲染，是因為 .top-navigation 的高度是
            寫死的（見 App.css）——少一顆按鈕不會讓橫線與側邊功能欄跳動。 */}
        {!hideLogout && (
          <button type="button" className="logout-btn" onClick={() => onLogout()}>
            登出
          </button>
        )}
      </div>
    </nav>
  );
}

export default Navbar;
