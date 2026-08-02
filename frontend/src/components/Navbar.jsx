function Navbar({ navigate, userName, onLogout, hideLogout = false }) {
  return (
    <nav className="top-navigation">
      <div className="brand-logo" onClick={() => navigate('/')} style={{ cursor: 'pointer' }}>
        Take a Bridge
      </div>
      <div className="top-navigation-actions">
        <div className="user-profile-name">{userName}</div>
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
