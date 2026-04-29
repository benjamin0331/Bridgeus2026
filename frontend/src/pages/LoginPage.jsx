import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './LoginPage.css';
import api from '../api/client';

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
      const response = await api.post('/api/token/', {
        username: userId, // Django 預設要找 'username' 這個欄位，我們把 userId 塞給它
        password: password
      });

      localStorage.setItem('access', response.data.access);
      localStorage.setItem('refresh', response.data.refresh);

      const nextUser = {
        name: userId,
        id: userId
      };

      localStorage.setItem('bridgeus_user', JSON.stringify(nextUser));
      setUser(nextUser);
      navigate('/', { replace: true });

    } catch (error) {
      console.error('登入失敗:', error);
      const status = error?.response?.status;

      if (status === 401) {
        setErrorMessage('帳號或密碼錯誤，請重新輸入！');
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
      {/* 登入卡片主體 */}
      <div className="login-card">
        <div className="login-header">
          <img src="/logo.png" alt="BridgeUs Logo" className="login-logo" />
          <h1>BridgeUs</h1>
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
      </div>
      
      {/* 背景裝飾元素 */}
      <div className="bg-decor bg-decor-1"></div>
      <div className="bg-decor bg-decor-2"></div>
      <div className="bg-decor bg-decor-3"></div>
    </div>
  );
}

export default LoginPage;
