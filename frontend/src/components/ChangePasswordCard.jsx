import { useState } from 'react';
import { changePassword } from '../api/auth';
import './ChangePasswordCard.css';

// 帳號安全 → 修改密碼。研究者與一般使用者共用同一張卡片：後端的
// /api/me/password/ 只認 request.user，改的永遠是自己的密碼，兩種身分沒有差別。
// 研究者要改「別人」的密碼走設定頁的帳號管理面板（/api/accounts/<id>/reset-password/）。

const EMPTY_FORM = { oldPassword: '', newPassword: '', newPasswordConfirm: '' };

// 後端的錯誤有兩種形狀：欄位錯誤 {new_password: ["…"]} 與整體錯誤
// {detail: "…"}。限流（429）只會回 detail，所以先看 detail 再逐欄位找。
const FIELD_ORDER = ['old_password', 'new_password', 'new_password_confirm'];

function extractError(requestError) {
  const data = requestError?.response?.data;
  if (typeof data?.detail === 'string') return data.detail;
  if (data && typeof data === 'object') {
    for (const key of [...FIELD_ORDER, ...Object.keys(data)]) {
      const value = data[key];
      if (Array.isArray(value) && value.length) return value[0];
      if (typeof value === 'string') return value;
    }
  }
  return '目前無法修改密碼，請稍後再試。';
}

function ChangePasswordCard({ username }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const updateField = (field) => (event) => {
    const { value } = event.target;
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setNotice('');

    // 兩次輸入不一致在前端先擋一次，省一趟往返；後端仍會再驗一次，
    // 這裡不是安全邊界。
    if (form.newPassword !== form.newPasswordConfirm) {
      setError('兩次輸入的新密碼不一致。');
      return;
    }

    setIsSubmitting(true);
    try {
      await changePassword({
        username,
        oldPassword: form.oldPassword,
        newPassword: form.newPassword,
        newPasswordConfirm: form.newPasswordConfirm,
      });
      setForm(EMPTY_FORM);
      setNotice('密碼已更新。其他裝置上的登入已失效，需要重新登入。');
    } catch (requestError) {
      setError(extractError(requestError));
    } finally {
      setIsSubmitting(false);
    }
  };

  const isIncomplete =
    !form.oldPassword || !form.newPassword || !form.newPasswordConfirm;

  return (
    <form className="settings-create-form change-password-card" onSubmit={handleSubmit}>
      <h2>修改密碼</h2>

      {notice ? <p className="settings-action-notice">{notice}</p> : null}

      <div className="change-password-fields">
        <label htmlFor="cp-old">
          目前的密碼
          <input
            id="cp-old"
            type={showPassword ? 'text' : 'password'}
            value={form.oldPassword}
            onChange={updateField('oldPassword')}
            autoComplete="current-password"
          />
        </label>

        <label htmlFor="cp-new">
          新密碼
          <input
            id="cp-new"
            type={showPassword ? 'text' : 'password'}
            value={form.newPassword}
            onChange={updateField('newPassword')}
            autoComplete="new-password"
          />
        </label>

        <label htmlFor="cp-confirm">
          再次輸入新密碼
          <input
            id="cp-confirm"
            type={showPassword ? 'text' : 'password'}
            value={form.newPasswordConfirm}
            onChange={updateField('newPasswordConfirm')}
            autoComplete="new-password"
          />
        </label>
      </div>

      <label className="settings-checkbox change-password-reveal">
        <input
          type="checkbox"
          checked={showPassword}
          onChange={(event) => setShowPassword(event.target.checked)}
        />
        顯示密碼
      </label>

      <span className="settings-hint">
        至少 8 個字，不能與帳號名稱太相似，也不能是常見密碼。
      </span>

      {error ? <p className="settings-action-error">{error}</p> : null}

      <div className="change-password-actions">
        <button type="submit" disabled={isSubmitting || isIncomplete}>
          {isSubmitting ? '更新中…' : '更新密碼'}
        </button>
      </div>
    </form>
  );
}

export default ChangePasswordCard;
