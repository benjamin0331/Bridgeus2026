import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import './LoginPage.css';
import { login, requestPasswordReset, confirmPasswordReset } from '../api/auth';

// 同一張登入卡片內切換：'login' 登入 → 'request' 輸入信箱 → 'confirm' 輸入
// 驗證碼與新密碼 → 'done' 完成。忘記密碼不換頁，直接在登入介面上進行。
const VIEW_LOGIN = 'login';
const VIEW_REQUEST = 'request';
const VIEW_CONFIRM = 'confirm';
const VIEW_DONE = 'done';

function LoginPage({ setUser, authMessage = '' }) {
  const navigate = useNavigate();
  const [view, setView] = useState(VIEW_LOGIN);

  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  // 忘記密碼流程的狀態，與登入表單分開。
  const [resetEmail, setResetEmail] = useState('');
  const [resetCode, setResetCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newPasswordConfirm, setNewPasswordConfirm] = useState('');
  const [showResetPassword, setShowResetPassword] = useState(false);
  const [resetError, setResetError] = useState('');
  const [resetFieldErrors, setResetFieldErrors] = useState({});
  const [resetNotice, setResetNotice] = useState('');
  const [resetSubmitting, setResetSubmitting] = useState(false);

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

  const openReset = () => {
    setResetError('');
    setResetFieldErrors({});
    setResetNotice('');
    setResetEmail('');
    setResetCode('');
    setNewPassword('');
    setNewPasswordConfirm('');
    setView(VIEW_REQUEST);
  };

  const backToLogin = () => {
    setView(VIEW_LOGIN);
    setErrorMessage('');
  };

  const handleResetRequest = async (e) => {
    e.preventDefault();
    setResetError('');
    setResetFieldErrors({});
    setResetSubmitting(true);

    try {
      await requestPasswordReset({ email: resetEmail });
      setResetNotice(
        '驗證碼已寄出，請查看信箱（含垃圾郵件匣）。驗證碼 10 分鐘內有效。',
      );
      setView(VIEW_CONFIRM);
    } catch (error) {
      const status = error?.response?.status;
      const detail = error?.response?.data?.detail;
      if (status === 404) {
        setResetError(detail || '這個信箱沒有註冊帳號。');
      } else if (status === 400) {
        setResetError('請輸入有效的電子信箱。');
      } else if (status === 429) {
        setResetError('索取驗證碼過於頻繁，請稍後再試。');
      } else if (status === 502) {
        setResetError(detail || '驗證碼寄送失敗，請稍後再試。');
      } else {
        setResetError('目前無法處理，請檢查網路連線後再試。');
      }
    } finally {
      setResetSubmitting(false);
    }
  };

  const handleResetConfirm = async (e) => {
    e.preventDefault();
    setResetError('');
    setResetFieldErrors({});
    setResetSubmitting(true);

    try {
      await confirmPasswordReset({
        email: resetEmail,
        code: resetCode.trim(),
        newPassword,
        newPasswordConfirm,
      });
      setView(VIEW_DONE);
    } catch (error) {
      const status = error?.response?.status;
      const data = error?.response?.data;

      if (status === 400 && data && typeof data === 'object') {
        // 驗證碼錯／過期只有一句 detail；密碼太弱等是逐欄位的。
        if (data.detail) {
          setResetError(
            Array.isArray(data.detail) ? data.detail.join(' ') : String(data.detail),
          );
        } else {
          setResetFieldErrors(data);
        }
      } else if (status === 429) {
        setResetError('嘗試過於頻繁，請稍後再試。');
      } else {
        setResetError('目前無法處理，請檢查網路連線後再試。');
      }
    } finally {
      setResetSubmitting(false);
    }
  };

  const resetFieldError = (name) => {
    const messages = resetFieldErrors[name];
    if (!messages) return null;
    const text = Array.isArray(messages) ? messages.join(' ') : String(messages);
    return <p className="login-reset-field-error">{text}</p>;
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
            {view === VIEW_LOGIN && <p>請輸入您的帳號密碼</p>}
            {view === VIEW_REQUEST && <p>輸入註冊時使用的電子信箱，我們會寄一組驗證碼給您</p>}
            {view === VIEW_CONFIRM && <p>輸入信箱收到的驗證碼並設定新密碼</p>}
            {view === VIEW_DONE && <p>密碼已重設完成</p>}
          </div>

          {view === VIEW_LOGIN && (
            <>
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

              <div className="login-forgot-link">
                <button type="button" className="login-link-btn" onClick={openReset}>
                  忘記密碼？
                </button>
              </div>

              <div className="login-register-link">
                還沒有帳號？<Link to="/register">立即註冊</Link>
              </div>
            </>
          )}

          {view === VIEW_REQUEST && (
            <>
              {resetError && <p className="login-reset-error">{resetError}</p>}

              <form className="login-form" onSubmit={handleResetRequest}>
                <div className="input-group">
                  <label>電子信箱</label>
                  <input
                    type="email"
                    className="login-input"
                    placeholder="you@example.com"
                    value={resetEmail}
                    onChange={(e) => setResetEmail(e.target.value)}
                    autoComplete="email"
                    required
                  />
                </div>

                <button
                  type="submit"
                  className="login-submit-btn"
                  disabled={resetSubmitting}
                >
                  {resetSubmitting ? '寄送中…' : '寄送驗證碼'}
                </button>
              </form>

              <div className="login-forgot-link">
                <button type="button" className="login-link-btn" onClick={backToLogin}>
                  返回登入
                </button>
              </div>
            </>
          )}

          {view === VIEW_CONFIRM && (
            <>
              {resetNotice && <p className="login-reset-notice">{resetNotice}</p>}
              {resetError && <p className="login-reset-error">{resetError}</p>}

              <form className="login-form" onSubmit={handleResetConfirm}>
                <div className="input-group">
                  <label>驗證碼</label>
                  <input
                    type="text"
                    className="login-input"
                    inputMode="numeric"
                    pattern="\d{6}"
                    maxLength={6}
                    placeholder="6 位數字"
                    value={resetCode}
                    onChange={(e) => setResetCode(e.target.value.replace(/\D/g, ''))}
                    autoComplete="one-time-code"
                    required
                  />
                  {resetFieldError('code')}
                </div>

                <div className="input-group">
                  <label>新密碼</label>
                  <input
                    type={showResetPassword ? 'text' : 'password'}
                    className="login-input"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    autoComplete="new-password"
                    required
                  />
                  {resetFieldError('new_password')}
                </div>

                <div className="input-group">
                  <label>再次輸入新密碼</label>
                  <input
                    type={showResetPassword ? 'text' : 'password'}
                    className="login-input"
                    value={newPasswordConfirm}
                    onChange={(e) => setNewPasswordConfirm(e.target.value)}
                    autoComplete="new-password"
                    required
                  />
                  {resetFieldError('new_password_confirm')}
                </div>

                <label className="login-reset-checkbox">
                  <input
                    type="checkbox"
                    checked={showResetPassword}
                    onChange={(e) => setShowResetPassword(e.target.checked)}
                  />
                  <span>顯示密碼</span>
                </label>

                <button
                  type="submit"
                  className="login-submit-btn"
                  disabled={resetSubmitting}
                >
                  {resetSubmitting ? '處理中…' : '設定新密碼'}
                </button>
              </form>

              <div className="login-forgot-link">
                <button
                  type="button"
                  className="login-link-btn"
                  onClick={handleResetRequest}
                  disabled={resetSubmitting}
                >
                  沒收到？重新寄送驗證碼
                </button>
                <span className="login-reset-sep">·</span>
                <button type="button" className="login-link-btn" onClick={backToLogin}>
                  返回登入
                </button>
              </div>
            </>
          )}

          {view === VIEW_DONE && (
            <>
              <p className="login-reset-notice">密碼已更新，請用新密碼登入。</p>
              <button
                type="button"
                className="login-submit-btn"
                style={{ width: '100%' }}
                onClick={backToLogin}
              >
                返回登入
              </button>
            </>
          )}
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
