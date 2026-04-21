function Navbar({ navigate, userId, onLogout }) {
  return (
    <nav className="top-navigation">
      <div className="brand-logo" onClick={() => navigate('/')} style={{ cursor: 'pointer' }}>
        BridgeUs
      </div>
      <div className="top-navigation-actions">
        <div className="user-profile-name">{userId}</div>
        <button type="button" className="logout-btn" onClick={onLogout}>
          登出
        </button>
      </div>
    </nav>
  );
}

export default Navbar;
