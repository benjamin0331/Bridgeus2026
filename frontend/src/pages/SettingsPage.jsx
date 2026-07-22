import './SettingsPage.css';

// 設定頁。目前只放研究者帳號管理；一般個人設定之後再加。齒輪對所有人可見，
// 非研究者進來看到佔位。實際存取控制在後端 IsResearcher，這裡只決定顯示。
function SettingsPage({ user }) {
  const isResearcher = Boolean(user?.isResearcher);

  if (!isResearcher) {
    return (
      <div className="settings-page">
        <div className="settings-heading">
          <h1>設定</h1>
        </div>
        <div className="settings-empty-card">尚無設定項。</div>
      </div>
    );
  }

  return (
    <div className="settings-page">
      <div className="settings-heading">
        <span className="settings-kicker">研究者</span>
        <h1>帳號管理</h1>
        <p>新增、停用／啟用帳號，設定研究者身分，或重設密碼。</p>
      </div>
      <div className="settings-empty-card">帳號管理面板即將載入…</div>
    </div>
  );
}

export default SettingsPage;
