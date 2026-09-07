import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import './LoginPage.css';
import { login } from '../api/auth';

function LoginPage({ setUser, authMessage = '' }) {
  const navigate = useNavigate();
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');

  // 🌟 2. 新增一個 state 來管理錯誤訊息 (比原本的 alert 更好看)
  const [errorMessage, setErrorMessage] = useState('');

  const handleLogin = async (e) => {
    e.preventDefault();
    setErrorMessage(''); // 每次按下登入先清空錯誤訊息

    try {
      const nextUser = await login({ username: userId, password });
      setUser(nextUser);
      navigate('/', { replace: true });

    } catch (error) {
      console.error('登入失敗:', error);
      const status = error?.response?.status;

      if (status === 401) {
        setErrorMessage('帳號或密碼錯誤，請重新輸入！');
      } else if (status === 429) {
        // 後端對 /api/token/ 掛了 ScopedRateThrottle（預設 10/min）。
        // 不轉述後端的秒數：DRF 的 Retry-After 是以整個時間窗計算的，
        // 對使用者來說「還要等 47 秒」跟事實不一定相符，講模糊一點反而誠實。
        setErrorMessage('登入嘗試過於頻繁，請稍候一分鐘再試。');
      } else if (status && status >= 500) {
        setErrorMessage('登入服務暫時無法使用，請稍後再試。');
      } else {
        setErrorMessage('目前無法登入，請檢查網路連線後再試。');
      }

      setPassword('');
    }
  };

  const displayErrorMessage = errorMessage || authMessage;

  return (
    <div className="login-page-container">
      {/* 卡片放在自己的捲動層：背景裝飾用負偏移定位，靠外層的
          overflow: hidden 裁切，那層不能拿來捲。 */}
      <div className="login-scroll-area">
        {/* 登入卡片主體 */}
        <div className="login-card">
          <div className="login-header">
            <img src="/logo.png" alt="TakeAbridge Logo" className="login-logo" />
            <h1>TakeAbridge</h1>
            <p>請輸入您的帳號密碼</p>
          </div>

          {/* 登入表單 */}
          <form className="login-form" onSubmit={handleLogin}>
            <div className="input-group">
              <label>User ID</label>
              <input
                type="text"
                className="login-input"
                placeholder="請輸入帳號"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                required
              />
            </div>

            <div className="input-group">
              <label>Password</label>
              <input
                type="password"
                className="login-input"
                placeholder="請輸入密碼"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>

            {displayErrorMessage && (
              <p style={{ color: 'red', fontSize: '14px', marginBottom: '10px' }}>
                {displayErrorMessage}
              </p>
            )}

            <button type="submit" className="login-submit-btn">
              進入討論
            </button>
          </form>

          {/* 底部輔助說明 */}
          <div className="login-footer">
            <p>請使用管理員帳號登入</p>
          </div>

          <div className="login-register-link">
            還沒有帳號？<Link to="/register">立即註冊</Link>
          </div>
        </div>
      </div>

      {/* 背景裝飾元素 */}
      <div className="bg-decor bg-decor-1"></div>
      <div className="bg-decor bg-decor-2"></div>
      <div className="bg-decor bg-decor-3"></div>
    </div>
  );
}

export default LoginPage;
