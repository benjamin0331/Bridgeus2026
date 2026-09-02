import { useEffect, useState } from 'react';
import api from '../api/client';
import './ProfileCard.css';

// 個人資料：顯示名稱與 email。兩者都靠 PATCH /api/me/ 存回去（白名單只有這
// 兩個欄位，username 與研究者身分不可自助修改）。
//
// 顯示名稱的可見範圍刻意很窄——不進對話室、不進 Godot 大廳（見
// accounts/tests_display_name_boundary.py）。實際上它目前哪裡都還沒被顯示，
// 只有 /api/me/ 會回給本人；知識庫署名是未來的用途。提示文字照這個現況寫，
// 不要先寫成「會出現在知識庫」——使用者會以為自己在對話中是具名的，而那
// 正好是這個平台不要的效果。

function extractError(requestError, fallback) {
  const data = requestError?.response?.data;
  if (typeof data?.detail === 'string') return data.detail;
  if (data && typeof data === 'object') {
    for (const key of ['display_name', 'email', ...Object.keys(data)]) {
      const value = data[key];
      if (Array.isArray(value) && value.length) return value[0];
      if (typeof value === 'string') return value;
    }
  }
  return fallback;
}

function ProfileCard({ username }) {
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  // 存檔時要判斷「這次到底改了什麼」：email 沒動就不送，避免使用者只改了
  // 顯示名稱卻被 email 的驗證訊息擋下來。
  const [loaded, setLoaded] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let cancelled = false;

    const loadProfile = async () => {
      setIsLoading(true);
      try {
        const response = await api.get('/api/me/');
        if (cancelled) return;
        const next = {
          displayName: response.data?.display_name ?? '',
          email: response.data?.email ?? '',
        };
        setDisplayName(next.displayName);
        setEmail(next.email);
        setLoaded(next);
      } catch (requestError) {
        if (cancelled) return;
        setError(extractError(requestError, '目前無法讀取個人資料。'));
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    void loadProfile();

    return () => {
      cancelled = true;
    };
  }, []);

  const isDirty =
    loaded !== null &&
    (displayName !== loaded.displayName || email.trim() !== loaded.email);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setNotice('');

    const payload = {};
    if (displayName !== loaded.displayName) payload.display_name = displayName;
    if (email.trim() !== loaded.email) payload.email = email.trim();
    if (Object.keys(payload).length === 0) return;

    setIsSaving(true);
    try {
      const response = await api.patch('/api/me/', payload);
      const next = {
        displayName: response.data?.display_name ?? '',
        email: response.data?.email ?? '',
      };
      setDisplayName(next.displayName);
      setEmail(next.email);
      setLoaded(next);
      setNotice('已儲存。');
    } catch (requestError) {
      setError(extractError(requestError, '儲存失敗，請再試一次。'));
    } finally {
      setIsSaving(false);
    }
  };

  const handleReset = () => {
    if (!loaded) return;
    setDisplayName(loaded.displayName);
    setEmail(loaded.email);
    setError('');
    setNotice('');
  };

  return (
    <form className="settings-create-form profile-card" onSubmit={handleSubmit}>
      <h2>個人資料</h2>

      {notice ? <p className="settings-action-notice">{notice}</p> : null}

      <div className="profile-fields">
        <label>
          帳號
          <input type="text" value={username ?? ''} disabled readOnly />
          <span className="settings-hint">帳號名稱不能修改。</span>
        </label>

        <label htmlFor="profile-display-name">
          顯示名稱
          <input
            id="profile-display-name"
            type="text"
            maxLength={50}
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            disabled={isLoading}
            placeholder="未設定"
          />
          <span className="settings-hint">
            目前只有你自己看得到。對話室與虛擬大廳一律匿名，對方看不到這個名字。
            留空就是不顯示。
          </span>
        </label>

        <label htmlFor="profile-email">
          Email
          <input
            id="profile-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={isLoading}
            placeholder="尚未設定"
          />
          <span className="settings-hint">
            用來識別你的帳號。目前不會寄送任何信件，也不會顯示給其他使用者。
          </span>
        </label>
      </div>

      {error ? <p className="settings-action-error">{error}</p> : null}

      <div className="profile-actions">
        <button type="submit" disabled={isLoading || isSaving || !isDirty}>
          {isSaving ? '儲存中…' : '儲存'}
        </button>
        <button
          type="button"
          onClick={handleReset}
          disabled={isLoading || isSaving || !isDirty}
        >
          取消
        </button>
      </div>
    </form>
  );
}

export default ProfileCard;
