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

const TABS = [
  { id: 'display', label: '顯示設定' },
  { id: 'videos', label: '影片管理' },
  { id: 'accounts', label: '帳號管理' },
];

function SettingsPage({ user }) {
  const isResearcher = Boolean(user?.isResearcher);
  const [activeTab, setActiveTab] = useState('display');

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

  // 知識庫影片管理
  const [videos, setVideos] = useState([]);
  const [isVideosLoading, setIsVideosLoading] = useState(false);
  const [videosError, setVideosError] = useState('');
  const [videoActionError, setVideoActionError] = useState('');
  const [videosReloadKey, setVideosReloadKey] = useState(0);
  const refreshVideos = () => setVideosReloadKey((key) => key + 1);

  const [newVideoTitle, setNewVideoTitle] = useState('');
  const [newVideoFile, setNewVideoFile] = useState(null);
  const [newVideoThumbnail, setNewVideoThumbnail] = useState('');
  const [newVideoDescription, setNewVideoDescription] = useState('');
  const [newVideoTopicId, setNewVideoTopicId] = useState('');
  const [isCreatingVideo, setIsCreatingVideo] = useState(false);
  // <input type="file"> 是 uncontrolled，選完檔案後要手動清空 value 才能
  // 重選同一個檔案再觸發一次 onChange；用 key 強制重新掛載最單純。
  const [videoFileInputKey, setVideoFileInputKey] = useState(0);

  // 顯示設定
  const [displaySettings, setDisplaySettings] = useState(null);
  const [displayError, setDisplayError] = useState('');
  const [displayNotice, setDisplayNotice] = useState('');
  const [displayReloadKey, setDisplayReloadKey] = useState(0);

  const refreshDisplaySettings = () => setDisplayReloadKey((key) => key + 1);

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

  useEffect(() => {
    if (!isResearcher) return undefined;

    let cancelled = false;
    const loadVideos = async () => {
      setIsVideosLoading(true);
      setVideosError('');
      try {
        const response = await api.get('/api/summary/videos/admin/');
        if (cancelled) return;
        setVideos(Array.isArray(response.data) ? response.data : []);
      } catch (requestError) {
        if (cancelled) return;
        setVideos([]);
        setVideosError(
          requestError?.response?.status === 403
            ? '這個功能只開放給研究者帳號使用。'
            : requestError?.response?.data?.detail || '目前無法讀取影片清單。'
        );
      } finally {
        if (!cancelled) setIsVideosLoading(false);
      }
    };

    void loadVideos();

    return () => {
      cancelled = true;
    };
  }, [isResearcher, videosReloadKey]);

  useEffect(() => {
    if (!isResearcher) return undefined;

    let cancelled = false;
    const loadDisplaySettings = async () => {
      setDisplayError('');
      try {
        const response = await api.get('/api/settings/display/');
        if (cancelled) return;
        setDisplaySettings(response.data);
      } catch (requestError) {
        if (cancelled) return;
        setDisplaySettings(null);
        setDisplayError(extractError(requestError, '目前無法讀取顯示設定。'));
      }
    };

    void loadDisplaySettings();

    return () => {
      cancelled = true;
    };
  }, [isResearcher, displayReloadKey]);

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

  const handleCreateVideo = async (event) => {
    event.preventDefault();
    if (isCreatingVideo) return;
    if (!newVideoFile) {
      setVideoActionError('請選擇要上傳的影片檔案。');
      return;
    }
    setIsCreatingVideo(true);
    setVideoActionError('');
    try {
      const formData = new FormData();
      formData.append('title', newVideoTitle);
      formData.append('video_file', newVideoFile);
      formData.append('thumbnail_url', newVideoThumbnail);
      formData.append('description', newVideoDescription);
      if (newVideoTopicId !== '') formData.append('topic_id', newVideoTopicId);
      await api.post('/api/summary/videos/admin/', formData);
      setNewVideoTitle('');
      setNewVideoFile(null);
      setVideoFileInputKey((key) => key + 1);
      setNewVideoThumbnail('');
      setNewVideoDescription('');
      setNewVideoTopicId('');
      refreshVideos();
    } catch (requestError) {
      setVideoActionError(extractError(requestError, '新增影片失敗，請再試一次。'));
    } finally {
      setIsCreatingVideo(false);
    }
  };

  const handleToggleVideoPublished = async (video) => {
    setVideoActionError('');
    try {
      await api.patch(`/api/summary/videos/admin/${video.id}/`, {
        is_published: !video.is_published,
      });
      refreshVideos();
    } catch (requestError) {
      setVideoActionError(extractError(requestError, '更新發布狀態失敗。'));
    }
  };

  const handleDeleteVideo = async (video) => {
    if (!window.confirm(`確定要刪除「${video.title}」嗎？此操作無法復原。`)) return;
    setVideoActionError('');
    try {
      await api.delete(`/api/summary/videos/admin/${video.id}/`);
      refreshVideos();
    } catch (requestError) {
      setVideoActionError(extractError(requestError, '刪除影片失敗。'));
    }
  };

  const patchPlatformSetting = async (payload) => {
    setDisplayError('');
    setDisplayNotice('');
    try {
      await api.patch('/api/settings/display/', payload);
      refreshDisplaySettings();
    } catch (requestError) {
      setDisplayError(extractError(requestError, '更新顯示設定失敗。'));
    }
  };

  const patchTopicSetting = async (topicId, payload) => {
    setDisplayError('');
    setDisplayNotice('');
    try {
      const response = await api.patch(
        `/api/settings/display/topics/${topicId}/`,
        payload,
      );
      if (response.data?.warning) setDisplayNotice(response.data.warning);
      refreshDisplaySettings();
    } catch (requestError) {
      setDisplayError(extractError(requestError, '更新議題設定失敗。'));
    }
  };

  const handleThresholdBlur = (topic, field, rawValue) => {
    const trimmed = String(rawValue).trim();
    // 空字串＝還原成程式碼預設值
    if (trimmed === '') {
      void patchTopicSetting(topic.topic_id, { [field]: null });
      return;
    }
    const parsed = Number(trimmed);
    if (Number.isNaN(parsed)) {
      setDisplayError('門檻需為數字。');
      return;
    }
    if (parsed === topic[field]) return;
    void patchTopicSetting(topic.topic_id, { [field]: parsed });
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
        <h1>研究者設定</h1>
        <p>調整前端顯示與議題開關，或管理帳號。</p>
      </div>

      <div className="settings-tab-row" role="tablist" aria-label="設定分類">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`settings-tab-btn${activeTab === tab.id ? ' is-active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'display' && displaySettings && (
        <section className="settings-display-panel">
          <h2>顯示設定</h2>

          {displayError && <div className="settings-action-error">{displayError}</div>}
          {displayNotice && <div className="settings-action-notice">{displayNotice}</div>}

          <div className="settings-display-row">
            <label>
              一般使用者入口
              <select
                value={displaySettings.platform.participant_entry_mode}
                onChange={(e) =>
                  patchPlatformSetting({ participant_entry_mode: e.target.value })
                }
              >
                <option value="mixed">混合入口（依立場自動分流）</option>
                <option value="split">分開入口（AI／配對各一）</option>
              </select>
            </label>

            <label>
              研究者入口
              <select
                value={displaySettings.platform.researcher_entry_mode}
                onChange={(e) =>
                  patchPlatformSetting({ researcher_entry_mode: e.target.value })
                }
              >
                <option value="mixed">混合入口（依立場自動分流）</option>
                <option value="split">分開入口（AI／配對各一）</option>
              </select>
            </label>

            <label>
              配對等待逾時（分鐘）
              <input
                type="number"
                min="1"
                max="120"
                key={displaySettings.platform.match_fallback_timeout_minutes}
                defaultValue={displaySettings.platform.match_fallback_timeout_minutes}
                onBlur={(e) => {
                  const parsed = Number(e.target.value);
                  if (
                    Number.isNaN(parsed) ||
                    parsed === displaySettings.platform.match_fallback_timeout_minutes
                  ) return;
                  void patchPlatformSetting({
                    match_fallback_timeout_minutes: parsed,
                  });
                }}
              />
            </label>
          </div>

          <table className="settings-table">
            <thead>
              <tr>
                <th>議題</th>
                <th>一般使用者可見</th>
                <th>研究者可見</th>
                <th>支持門檻</th>
                <th>反對門檻</th>
              </tr>
            </thead>
            <tbody>
              {displaySettings.topics.map((topic) => (
                <tr key={topic.topic_id}>
                  <td>{topic.title}</td>
                  <td>
                    <input
                      type="checkbox"
                      checked={topic.visible_to_participant}
                      onChange={(e) =>
                        patchTopicSetting(topic.topic_id, {
                          visible_to_participant: e.target.checked,
                        })
                      }
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={topic.visible_to_researcher}
                      onChange={(e) =>
                        patchTopicSetting(topic.topic_id, {
                          visible_to_researcher: e.target.checked,
                        })
                      }
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      step="0.1"
                      key={`sup-${topic.topic_id}-${topic.support_threshold}`}
                      defaultValue={topic.support_threshold}
                      onBlur={(e) =>
                        handleThresholdBlur(topic, 'support_threshold', e.target.value)
                      }
                    />
                    <span className="settings-hint">
                      預設 {topic.default_support_threshold}
                    </span>
                  </td>
                  <td>
                    <input
                      type="number"
                      step="0.1"
                      key={`opp-${topic.topic_id}-${topic.oppose_threshold}`}
                      defaultValue={topic.oppose_threshold}
                      onBlur={(e) =>
                        handleThresholdBlur(topic, 'oppose_threshold', e.target.value)
                      }
                    />
                    <span className="settings-hint">
                      預設 {topic.default_oppose_threshold}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="settings-hint">
            門檻欄位清空後離開輸入框，即還原成程式碼預設值。變更門檻只影響之後填寫的問卷。
          </p>
        </section>
      )}

      {activeTab === 'videos' && (
      <>
      <h2>觀點知識庫影片管理</h2>

      <form className="settings-create-form" onSubmit={handleCreateVideo}>
        <h2>新增影片</h2>
        <div className="settings-create-row">
          <input
            type="text"
            placeholder="影片標題"
            value={newVideoTitle}
            onChange={(e) => setNewVideoTitle(e.target.value)}
            required
          />
          <input
            key={videoFileInputKey}
            type="file"
            accept="video/*"
            onChange={(e) => setNewVideoFile(e.target.files?.[0] ?? null)}
            required
          />
          <input
            type="url"
            placeholder="縮圖網址（選填）"
            value={newVideoThumbnail}
            onChange={(e) => setNewVideoThumbnail(e.target.value)}
          />
          <select
            value={newVideoTopicId}
            onChange={(e) => setNewVideoTopicId(e.target.value)}
          >
            <option value="">不限議題</option>
            {(displaySettings?.topics || []).map((topic) => (
              <option key={topic.topic_id} value={topic.topic_id}>
                {topic.title}
              </option>
            ))}
          </select>
          <button type="submit" disabled={isCreatingVideo}>
            {isCreatingVideo ? '上傳中…' : '上傳'}
          </button>
        </div>
        <textarea
          className="settings-video-description"
          placeholder="影片說明（選填）"
          value={newVideoDescription}
          onChange={(e) => setNewVideoDescription(e.target.value)}
          rows={2}
        />
      </form>

      {videoActionError && <div className="settings-action-error">{videoActionError}</div>}

      <div className="settings-list">
        {isVideosLoading && <div className="settings-empty-card">正在讀取…</div>}
        {!isVideosLoading && videosError && (
          <div className="settings-empty-card error">{videosError}</div>
        )}
        {!isVideosLoading && !videosError && videos.length === 0 && (
          <div className="settings-empty-card">目前沒有上傳任何影片。</div>
        )}
        {!isVideosLoading && !videosError && videos.length > 0 && (
          <table className="settings-table">
            <thead>
              <tr>
                <th>標題</th>
                <th>議題</th>
                <th>狀態</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {videos.map((video) => (
                <tr key={video.id}>
                  <td>
                    <a href={video.url} target="_blank" rel="noopener noreferrer">
                      {video.title}
                    </a>
                  </td>
                  <td>
                    {video.topic_id == null
                      ? '不限議題'
                      : displaySettings?.topics?.find((t) => t.topic_id === video.topic_id)
                          ?.title || `議題 ${video.topic_id}`}
                  </td>
                  <td>{video.is_published ? '已發布' : '未發布'}</td>
                  <td className="settings-actions">
                    <button type="button" onClick={() => handleToggleVideoPublished(video)}>
                      {video.is_published ? '取消發布' : '發布'}
                    </button>
                    <button type="button" onClick={() => handleDeleteVideo(video)}>
                      刪除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      </>
      )}

      {activeTab === 'accounts' && (
      <>
      <h2>帳號管理</h2>

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
      </>
      )}
    </div>
  );
}

export default SettingsPage;
