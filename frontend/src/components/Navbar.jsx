function Navbar({ navigate, userName, onLogout, hideLogout = false }) {
  return (
    <nav className="top-navigation">
      <div className="brand-logo" onClick={() => navigate('/')} style={{ cursor: 'pointer' }}>
        Take a Bridge
      </div>
      <div className="top-navigation-actions">
        <div className="user-profile-name">{userName}</div>
        {/* 對話頁不給登出，但按鈕要留在版面裡：整條導覽列的高度是這顆按鈕撐出
            來的，直接不渲染會讓橫線、連同底下的側邊功能欄在對話頁往上跑約
            10px，「Take a Bridge」與使用者名稱也跟著移位。 */}
        <button
          type="button"
          className={`logout-btn${hideLogout ? ' is-hidden' : ''}`}
          onClick={() => onLogout()}
          disabled={hideLogout}
          aria-hidden={hideLogout || undefined}
        >
          登出
        </button>
      </div>
    </nav>
  );
}

export default Navbar;
