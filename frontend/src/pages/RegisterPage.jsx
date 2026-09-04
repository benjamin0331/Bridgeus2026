import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import './RegisterPage.css';
import { register } from '../api/auth';

// 後端 accounts/consent.py 的 CONSENT_DOCUMENT_PATH。兩邊都是常數，
// 改動時要一起改——目前沒有把後端常數送到前端的機制，為了一個字串
// 不值得新增一支端點。
const CONSENT_DOCUMENT_PATH = '/consent';

const EMPTY_FIELD_ERRORS = {};

function RegisterPage({ setUser }) {
  const navigate = useNavigate();
  const [form, setForm] = useState({
    username: '',
    email: '',
    password: '',
    password_confirm: '',
    display_name: '',
  });
  const [consent, setConsent] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [fieldErrors, setFieldErrors] = useState(EMPTY_FIELD_ERRORS);
  const [generalError, setGeneralError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const updateField = (name) => (event) => {
    setForm((prev) => ({ ...prev, [name]: event.target.value }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setFieldErrors(EMPTY_FIELD_ERRORS);
    setGeneralError('');
    setSubmitting(true);

    try {
      const nextUser = await register({ ...form, consent });
      setUser(nextUser);
      navigate('/', { replace: true });
    } catch (error) {
      const status = error?.response?.status;
      const data = error?.response?.data;

      if (status === 400 && data && typeof data === 'object') {
        // DRF 的欄位錯誤是 { 欄位: [訊息, ...] }。逐欄位顯示，使用者才知道
        // 要改哪一格；只丟一句「註冊失敗」等於要他們自己猜。
        setFieldErrors(data);
      } else if (status === 429) {
        setGeneralError('註冊嘗試過於頻繁，請稍候再試。');
      } else if (status && status >= 500) {
        setGeneralError('註冊服務暫時無法使用，請稍後再試。');
      } else {
        setGeneralError('目前無法註冊，請檢查網路連線後再試。');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const errorFor = (name) => {
    const messages = fieldErrors[name];
    if (!messages) return null;
    const text = Array.isArray(messages) ? messages.join(' ') : String(messages);
    return <span className="register-field-error">{text}</span>;
  };

  return (
    <div className="register-page-container">
      <div className="register-card">
        <div className="register-header">
          <h1>建立帳號</h1>
          <p>歡迎加入 TakeAbridge</p>
        </div>

        {generalError && <div className="register-error">{generalError}</div>}

        <form onSubmit={handleSubmit}>
          <div className="register-field">
            <label htmlFor="username">帳號名稱</label>
            <input
              id="username"
              type="text"
              value={form.username}
              onChange={updateField('username')}
              autoComplete="username"
              required
            />
            {errorFor('username')}
          </div>

          <div className="register-field">
            <label htmlFor="email">電子信箱</label>
            <input
              id="email"
              type="email"
              value={form.email}
              onChange={updateField('email')}
              autoComplete="email"
              required
            />
            {errorFor('email')}
          </div>

          <div className="register-field">
            <label htmlFor="display_name">顯示名稱（選填）</label>
            <input
              id="display_name"
              type="text"
              value={form.display_name}
              onChange={updateField('display_name')}
              maxLength={50}
            />
            {errorFor('display_name')}
          </div>

          <div className="register-field">
            <label htmlFor="password">密碼</label>
            <input
              id="password"
              type={showPassword ? 'text' : 'password'}
              value={form.password}
              onChange={updateField('password')}
              autoComplete="new-password"
              required
            />
            {errorFor('password')}
          </div>

          <div className="register-field">
            <label htmlFor="password_confirm">再次輸入密碼</label>
            <input
              id="password_confirm"
              type={showPassword ? 'text' : 'password'}
              value={form.password_confirm}
              onChange={updateField('password_confirm')}
              autoComplete="new-password"
              required
            />
            {errorFor('password_confirm')}
          </div>

          {/* 沒有「忘記密碼」流程，打錯密碼的代價是要麻煩研究者重設，
              所以提供顯示切換讓使用者自己確認。 */}
          <label className="register-consent">
            <input
              type="checkbox"
              checked={showPassword}
              onChange={(event) => setShowPassword(event.target.checked)}
            />
            <span>顯示密碼</span>
          </label>

          <label className="register-consent">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
            />
            <span>
              我已閱讀並同意{' '}
              <Link to={CONSENT_DOCUMENT_PATH} target="_blank" rel="noreferrer">
                研究參與說明
              </Link>
            </span>
          </label>
          {errorFor('consent')}

          <button
            className="register-submit"
            type="submit"
            disabled={!consent || submitting}
          >
            {submitting ? '註冊中…' : '建立帳號'}
          </button>
        </form>

        <div className="register-footer">
          已經有帳號了？<Link to="/">返回登入</Link>
        </div>
      </div>
    </div>
  );
}

export default RegisterPage;
