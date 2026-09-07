import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import './ForgotPasswordPage.css';
import { requestPasswordReset, confirmPasswordReset } from '../api/auth';

// 'request' = 輸入信箱請寄碼；'confirm' = 輸入碼與新密碼；'done' = 完成。
function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState('request');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});
  const [generalError, setGeneralError] = useState('');
  const [notice, setNotice] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const errorFor = (name) => {
    const messages = fieldErrors[name];
    if (!messages) return null;
    const text = Array.isArray(messages) ? messages.join(' ') : String(messages);
    return <span className="forgot-field-error">{text}</span>;
  };

  const handleRequest = async (event) => {
    event.preventDefault();
    setFieldErrors({});
    setGeneralError('');
    setSubmitting(true);

    try {
      await requestPasswordReset({ email });
      // 後端不論信箱有沒有註冊都回同一句話——這裡也照樣往下一步走，
      // 不暗示「這個信箱存在」。
      setNotice('如果這個信箱有註冊帳號，我們已經寄出一組 6 位數驗證碼，請查看信箱（含垃圾郵件匣）。驗證碼 10 分鐘內有效。');
      setStep('confirm');
    } catch (error) {
      const status = error?.response?.status;
      if (status === 400) {
        setGeneralError('請輸入有效的電子信箱。');
      } else if (status === 429) {
        setGeneralError('索取驗證碼過於頻繁，請稍後再試。');
      } else {
        setGeneralError('目前無法處理，請檢查網路連線後再試。');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleConfirm = async (event) => {
    event.preventDefault();
    setFieldErrors({});
    setGeneralError('');
    setSubmitting(true);

    try {
      await confirmPasswordReset({
        email,
        code: code.trim(),
        newPassword: password,
        newPasswordConfirm: passwordConfirm,
      });
      setStep('done');
    } catch (error) {
      const status = error?.response?.status;
      const data = error?.response?.data;

      if (status === 400 && data && typeof data === 'object') {
        // 驗證碼錯 / 過期只有一句 detail；密碼太弱等是逐欄位的。
        if (data.detail) {
          setGeneralError(
            Array.isArray(data.detail) ? data.detail.join(' ') : String(data.detail),
          );
        } else {
          setFieldErrors(data);
        }
      } else if (status === 429) {
        setGeneralError('嘗試過於頻繁，請稍後再試。');
      } else {
        setGeneralError('目前無法處理，請檢查網路連線後再試。');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="forgot-page-container">
      <div className="forgot-card">
        <div className="forgot-header">
          <h1>重設密碼</h1>
          {step === 'request' && <p>輸入註冊時使用的電子信箱，我們會寄一組驗證碼給您。</p>}
          {step === 'confirm' && <p>輸入信箱收到的驗證碼，並設定新密碼。</p>}
        </div>

        {generalError && <div className="forgot-error">{generalError}</div>}
        {notice && step === 'confirm' && <div className="forgot-notice">{notice}</div>}

        {step === 'request' && (
          <form onSubmit={handleRequest}>
            <div className="forgot-field">
              <label htmlFor="email">電子信箱</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                required
              />
            </div>
            <button className="forgot-submit" type="submit" disabled={submitting}>
              {submitting ? '寄送中…' : '寄送驗證碼'}
            </button>
          </form>
        )}

        {step === 'confirm' && (
          <form onSubmit={handleConfirm}>
            <div className="forgot-field">
              <label htmlFor="code">驗證碼</label>
              <input
                id="code"
                type="text"
                inputMode="numeric"
                pattern="\d{6}"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                autoComplete="one-time-code"
                required
              />
              {errorFor('code')}
            </div>

            <div className="forgot-field">
              <label htmlFor="new_password">新密碼</label>
              <input
                id="new_password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                required
              />
              {errorFor('new_password')}
            </div>

            <div className="forgot-field">
              <label htmlFor="new_password_confirm">再次輸入新密碼</label>
              <input
                id="new_password_confirm"
                type={showPassword ? 'text' : 'password'}
                value={passwordConfirm}
                onChange={(e) => setPasswordConfirm(e.target.value)}
                autoComplete="new-password"
                required
              />
              {errorFor('new_password_confirm')}
            </div>

            <label className="forgot-checkbox">
              <input
                type="checkbox"
                checked={showPassword}
                onChange={(e) => setShowPassword(e.target.checked)}
              />
              <span>顯示密碼</span>
            </label>

            <button className="forgot-submit" type="submit" disabled={submitting}>
              {submitting ? '處理中…' : '設定新密碼'}
            </button>

            <button
              type="button"
              className="forgot-resend"
              onClick={handleRequest}
              disabled={submitting}
            >
              沒收到？重新寄送驗證碼
            </button>
          </form>
        )}

        {step === 'done' && (
          <div className="forgot-done">
            <p>密碼已更新，請用新密碼登入。</p>
            <button
              className="forgot-submit"
              type="button"
              onClick={() => navigate('/', { replace: true })}
            >
              前往登入
            </button>
          </div>
        )}

        <div className="forgot-footer">
          想起來了？<Link to="/">返回登入</Link>
        </div>
      </div>
    </div>
  );
}

export default ForgotPasswordPage;
