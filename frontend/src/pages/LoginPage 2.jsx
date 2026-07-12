import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './LoginPage.css';

function LoginPage({ setUser }) {
  const navigate = useNavigate();
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');

  // 靜態帳號資料，後續開發可替換為後端 API 驗證
  const MASTER_CREDENTIALS = {
    id: 'ditto_01',
    password: 'bridgeus',
    name: '芋頭精靈'
  };

  // 處理表單提交邏輯
  const handleLogin = (e) => {
    e.preventDefault();

    // 進行帳號與密碼比對
    if (userId === MASTER_CREDENTIALS.id && password === MASTER_CREDENTIALS.password) {
      // 驗證成功：更新全域使用者狀態並跳轉至首頁
      setUser({ 
        name: MASTER_CREDENTIALS.name, 
        id: MASTER_CREDENTIALS.id 
      });
      navigate('/');
    } else {
      // 驗證失敗：彈出警示並清空密碼欄位
      alert("帳號或密碼錯誤，請重新輸入！");
      setPassword(''); 
    }
  };

  return (
    <div className="login-page-container">
      {/* 登入卡片主體 */}
      <div className="login-card">
        <div className="login-header">
          <img src="/logo.png" alt="Take A Bridge Logo" className="login-logo" />
          <h1>Take A Bridge</h1>
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

          <button type="submit" className="login-submit-btn">
            進入討論
          </button>
        </form>

        {/* 底部輔助說明 */}
        <div className="login-footer">
          <p>預設帳密：ditto_01 / bridgeus</p>
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