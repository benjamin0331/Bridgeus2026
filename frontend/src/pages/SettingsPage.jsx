import { useEffect, useState } from 'react';
import api from '../api/client';
import './SettingsPage.css';

// 設定頁。研究者帳號管理面板；一般個人設定之後再加。實際存取控制在後端
// IsResearcher，這裡只決定顯示，非研究者看到佔位。

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-TW', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function SettingsPage({ user }) {
  const isResearcher = Boolean(user?.isResearcher);

  const [accounts, setAccounts] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  // 每次操作後 bump 一下觸發重新載入（比在事件裡直接呼叫 loader 乾淨，
  // 也避免 effect 內同步呼叫 setState 的 lint 問題）。
  const [reloadKey, setReloadKey] = useState(0);

  // 新增帳號表單
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newIsResearcher, setNewIsResearcher] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

  const refreshAccounts = () => setReloadKey((key) => key + 1);

  useEffect(() => {
    if (!isResearcher) return undefined;

    let cancelled = false;
    const loadAccounts = async () => {
      setIsLoading(true);
      setError('');
      try {
        const response = await api.get('/api/accounts/');
        if (cancelled) return;
        setAccounts(Array.isArray(response.data) ? response.data : []);
      } catch (requestError) {
        if (cancelled) return;
        setAccounts([]);
        setError(
          requestError?.response?.status === 403
            ? '這個頁面只開放給研究者帳號使用。'
            : requestError?.response?.data?.detail || '目前無法讀取帳號清單。'
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    void loadAccounts();

    return () => {
      cancelled = true;
    };
  }, [isResearcher, reloadKey]);

  const extractError = (requestError, fallback) => {
    const data = requestError?.response?.data;
    if (data?.detail) return data.detail;
    if (data && typeof data === 'object') {
      const firstKey = Object.keys(data)[0];
      const firstVal = firstKey ? data[firstKey] : null;
      if (Array.isArray(firstVal)) return firstVal[0];
      if (typeof firstVal === 'string') return firstVal;
    }
    return fallback;
  };

  const handleCreate = async (event) => {
    event.preventDefault();
    if (isCreating) return;
    setIsCreating(true);
    setActionError('');
    try {
      await api.post('/api/accounts/', {
        username: newUsername,
        password: newPassword,
        is_researcher: newIsResearcher,
      });
      setNewUsername('');
      setNewPassword('');
      setNewIsResearcher(false);
      refreshAccounts();
    } catch (requestError) {
      setActionError(extractError(requestError, '新增帳號失敗，請再試一次。'));
    } finally {
      setIsCreating(false);
    }
  };

  const patchAccount = async (account, payload, fallbackMsg) => {
    setActionError('');
    try {
      await api.patch(`/api/accounts/${account.id}/`, payload);
      refreshAccounts();
    } catch (requestError) {
      setActionError(extractError(requestError, fallbackMsg));
    }
  };

  const handleToggleActive = (account) =>
    patchAccount(account, { is_active: !account.is_active }, '更新狀態失敗。');

  const handleToggleResearcher = (account) =>
    patchAccount(account, { is_researcher: !account.is_researcher }, '更新研究者身分失敗。');

  const handleResetPassword = async (account) => {
    const next = window.prompt(`為「${account.username}」設定新密碼：`);
    if (!next) return;
    setActionError('');
    try {
      await api.post(`/api/accounts/${account.id}/reset-password/`, { password: next });
      window.alert('密碼已重設。');
    } catch (requestError) {
      setActionError(extractError(requestError, '重設密碼失敗。'));
    }
  };

  if (!isResearcher) {
    return (
      <div className="settings-page">
        <div className="settings-heading"><h1>設定</h1></div>
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

      <form className="settings-create-form" onSubmit={handleCreate}>
        <h2>新增帳號</h2>
        <div className="settings-create-row">
          <input
            type="text"
            placeholder="帳號名稱"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            required
          />
          <input
            type="text"
            placeholder="初始密碼"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
          />
          <label className="settings-checkbox">
            <input
              type="checkbox"
              checked={newIsResearcher}
              onChange={(e) => setNewIsResearcher(e.target.checked)}
            />
            設為研究者
          </label>
          <button type="submit" disabled={isCreating}>建立</button>
        </div>
      </form>

      {actionError && <div className="settings-action-error">{actionError}</div>}

      <div className="settings-list">
        {isLoading && <div className="settings-empty-card">正在讀取…</div>}
        {!isLoading && error && <div className="settings-empty-card error">{error}</div>}
        {!isLoading && !error && accounts.length === 0 && (
          <div className="settings-empty-card">目前沒有帳號。</div>
        )}
        {!isLoading && !error && accounts.length > 0 && (
          <table className="settings-table">
            <thead>
              <tr>
                <th>帳號</th>
                <th>狀態</th>
                <th>研究者</th>
                <th>最後登入</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.id}>
                  <td>{account.username}{account.is_superuser ? '（管理員）' : ''}</td>
                  <td>{account.is_active ? '啟用' : '停用'}</td>
                  <td>{account.is_researcher ? '是' : '否'}</td>
                  <td>{formatTime(account.last_login)}</td>
                  <td className="settings-actions">
                    <button
                      type="button"
                      disabled={account.is_superuser}
                      onClick={() => handleToggleActive(account)}
                    >
                      {account.is_active ? '停用' : '啟用'}
                    </button>
                    <button
                      type="button"
                      disabled={account.is_superuser}
                      onClick={() => handleToggleResearcher(account)}
                    >
                      {account.is_researcher ? '取消研究者' : '設為研究者'}
                    </button>
                    <button
                      type="button"
                      disabled={account.is_superuser}
                      onClick={() => handleResetPassword(account)}
                    >
                      重設密碼
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default SettingsPage;
