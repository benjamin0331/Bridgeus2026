import { useState, useEffect, useCallback, useRef } from 'react'
import { Routes, Route, useNavigate, useParams, useLocation } from 'react-router-dom'
import './App.css'

import api, { AUTH_LOGOUT_EVENT, clearAuthStorage } from './api/client'
import { NotificationsProvider } from './context/NotificationsContext'
import { MatchingHeartbeatProvider } from './context/MatchingHeartbeatContext'
import Navbar from './components/Navbar'
import Sidebar from './components/Sidebar'
import HomePage from './pages/HomePage'
import TopicChat from './pages/TopicChat'
import KnowledgeBase from './pages/KnowledgeBase'
import KnowledgeBaseTopicPage from './pages/KnowledgeBaseTopicPage'
import KnowledgeBaseConversationPage from './pages/KnowledgeBaseConversationPage'
import FavoritesPage from './pages/FavoritesPage'
import HistoryPage from './pages/HistoryPage'
import NotificationPage from './pages/NotificationPage'
import AchievementPage from './pages/AchievementPage'
import { ackAchievements, fetchAchievements } from './api/achievements'
import AchievementToast from './components/AchievementToast'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import ConsentPage from './pages/ConsentPage'
import PostQuestionnairePage from './pages/PostQuestionnairePage'
import DebriefingPage from './pages/DebriefingPage'
import PlatformFeedbackPage from './pages/PlatformFeedbackPage'
import ViewpointReviewPage from './pages/ViewpointReviewPage'
import SettingsPage from './pages/SettingsPage'
import GodotLobby from './pages/GodotLobby'

function TopicChatRoute({ user, issues, issuesLoaded, entryMode }) {
  const { id } = useParams();
  const location = useLocation();

  // entryMode 還沒回來就先不掛載。TopicChat 在第一次 render 就會把模式定下來
  // （resolvedMode 是 useState 的初始值，之後不跟著 entryMode 走），所以晚到
  // 的伺服器答案救不回來——直接開 /topic/x?mode=match 的書籤會整頁卡在配對
  // 模式，即使伺服器說這個人是混合入口。寧可晚一個 request 再畫。
  if (!entryMode) {
    // 跟 TopicChat 自己的載入畫面同一個樣子，避免兩段等待看起來像兩件事。
    return <div style={{ padding: '50px', textAlign: 'center' }}>正在載入議題數據...</div>;
  }

  return (
    <TopicChat
      key={`${id}-${location.search}`}
      user={user}
      issues={issues}
      issuesLoaded={issuesLoaded}
      entryMode={entryMode}
    />
  );
}

function App() {
  const navigate = useNavigate();
  const location = useLocation();

  const [user, setUser] = useState(() => {
    try {
      if (!localStorage.getItem('access')) {
        clearAuthStorage();
        return null;
      }

      const storedUser = localStorage.getItem('bridgeus_user');
      return storedUser ? JSON.parse(storedUser) : null;
    } catch (error) {
      console.error('Failed to restore user session:', error);
      clearAuthStorage();
      return null;
    }
  });
  const [authMessage, setAuthMessage] = useState('');
  const [issues, setIssues] = useState([]);
  const [issuesLoaded, setIssuesLoaded] = useState(false);
  // null = /api/me/ 還沒回來（不是「分開入口」）。讀取失敗時會設成 'split'，
  // 所以 null 只代表「還在等」，不會永久卡住。TopicChatRoute 靠這個分辨。
  const [entryMode, setEntryMode] = useState(null);

  // 待跳的解鎖通知佇列。後端以 UserAchievement.notified_at 為準，跳完才 ack，
  // 所以重整不會重跳，換瀏覽器也不會。
  const [toastQueue, setToastQueue] = useState([]);
  const unlockedToast = toastQueue[0] ?? null;

  // 本次登入期間已經跳過、且已送出 ack 的 code。
  //
  // 為什麼需要它：ack 是非同步的，而下面那個 effect 依賴 location.pathname，
  // 換頁就會立刻重新 fetch。點 toast 的「查看」時 dismissToast() 之後緊接著
  // navigate('/achievement')，ack 幾乎不可能在那之前落庫，於是後端仍會把同一筆
  // 算進 newly_unlocked，通知就在成就頁上又跳一次。
  //
  // 不用「await ack 再導頁」的原因：那會讓每次點「查看」都多等一個網路來回，
  // 而且 ack 失敗時還是會重跳。本地清單兩種情況都擋得住。
  const acknowledgedRef = useRef(new Set());

  // 依賴帶 location.pathname 而不只是 user：最主要的解鎖時機是送出後測，那發生在
  // session 中間，而後測頁送完是 SPA 導頁（App 不會 remount）。只看 user 的話，
  // 通知會晚整整一個 session 才跳，而且是在使用者早就從成就頁看到卡片解鎖之後 ——
  // 等於通知永遠是舊聞。每次換頁多一支 GET（後端會順便結算），在這個規模可接受。
  useEffect(() => {
    if (!user) return undefined;
    let cancelled = false;
    fetchAchievements()
      .then((data) => {
        if (cancelled) return;
        const pending = (data.newly_unlocked ?? []).filter(
          (item) => !acknowledgedRef.current.has(item.code),
        );
        setToastQueue(pending);
      })
      .catch((err) => {
        // 通知拿不到不影響任何功能，記著就好。
        console.warn('成就通知讀取失敗：', err?.message ?? err);
      });
    return () => { cancelled = true; };
  }, [user, location.pathname]);

  // 副作用留在 updater 外：StrictMode 會把 setState 的 updater 跑兩次，ack 放進去
  // 就會送兩次。當前值改從閉包讀，所以依賴陣列要帶 toastQueue。
  const dismissToast = useCallback(() => {
    const shown = toastQueue[0];
    if (shown) {
      acknowledgedRef.current.add(shown.code);
      ackAchievements([shown.code])
        .catch((err) => console.warn('成就通知標記已讀失敗：', err?.message ?? err));
    }
    setToastQueue((queue) => queue.slice(1));
  }, [toastQueue]);

  const handleLogin = useCallback((nextUser) => {
    setAuthMessage('');
    setIssues([]);
    setIssuesLoaded(false);
    setUser(nextUser);
  }, []);

  const handleLogout = useCallback((message = '') => {
    clearAuthStorage();
    setAuthMessage(message);
    setUser(null);
    setIssues([]);
    setIssuesLoaded(false);
    // 登出一定要清掉待跳的成就通知：留著的話下一位登入者會在 fetchAchievements()
    // 回來前的空檔看到上一位的 toast，點下去還會以自己的身份 ack，把自己同 code
    // 的成就誤標成已通知（共用機器的實驗環境下會真的發生）。
    setToastQueue([]);
    // 本地已 ack 清單也要清：它是以 code 為 key 的，留著會讓下一位登入者
    // 同 code 的新解鎖被誤判成「已經跳過了」而永遠不顯示。
    acknowledgedRef.current = new Set();
    setEntryMode('split');
    navigate('/', { replace: true });
  }, [navigate]);

  useEffect(() => {
    if (user) {
      localStorage.setItem('bridgeus_user', JSON.stringify(user));
      return;
    }

    localStorage.removeItem('bridgeus_user');
  }, [user]);

  useEffect(() => {
    const handleAuthLogout = (event) => {
      handleLogout(event.detail?.message || '登入已過期，請重新登入。');
    };

    window.addEventListener(AUTH_LOGOUT_EVENT, handleAuthLogout);
    return () => window.removeEventListener(AUTH_LOGOUT_EVENT, handleAuthLogout);
  }, [handleLogout]);

  useEffect(() => {
    if (!user) {
      return undefined;
    }

    let cancelled = false;

    const fetchIssuesFromBackend = async () => {
      try {
        const response = await api.get('/api/dialogue/topics/');
        if (!cancelled) {
          setIssues(Array.isArray(response.data) ? response.data : []);
        }
      } catch (error) {
        console.error('Data fetching error:', error);
        if (!cancelled) {
          setIssues([]);
        }
      } finally {
        if (!cancelled) {
          setIssuesLoaded(true);
        }
      }
    };

    fetchIssuesFromBackend();

    return () => {
      cancelled = true;
    };
  }, [user]);

  useEffect(() => {
    // 登出時的重設交給 handleLogout，不在 effect 內同步 setState
    // （react-hooks/set-state-in-effect），跟 issues/issuesLoaded 同一個模式。
    if (!user) {
      return undefined;
    }

    let cancelled = false;

    const fetchEntryMode = async () => {
      try {
        const response = await api.get('/api/me/');
        if (!cancelled) {
          setEntryMode(response.data?.entry_mode === 'mixed' ? 'mixed' : 'split');
        }
      } catch (error) {
        console.error('Failed to load entry mode:', error);
        // 讀不到就退回分開入口：兩個入口都看得到，比整個消失好。
        if (!cancelled) setEntryMode('split');
      }
    };

    void fetchEntryMode();

    return () => {
      cancelled = true;
    };
  }, [user]);

  if (!user) {
    return (
      <Routes>
        {/* 這兩條必須排在 catch-all 之前，否則 path="*" 會把它們吃掉。 */}
        <Route path="/register" element={<RegisterPage setUser={handleLogin} />} />
        <Route path="/consent" element={<ConsentPage />} />
        <Route path="*" element={<LoginPage setUser={handleLogin} authMessage={authMessage} />} />
      </Routes>
    );
  }

  const isTopicPage = location.pathname.startsWith('/topic/');

  return (
    <NotificationsProvider user={user}>
      <MatchingHeartbeatProvider>
      <div className="app-container">
        <Navbar
          navigate={navigate}
          userName={user.name || user.username || user.id}
          onLogout={handleLogout}
          hideLogout={isTopicPage}
        />

        <div className="main-layout-wrapper">
          <div className="content-area">
            <Routes>
              <Route
                path="/"
                element={(
                  <HomePage
                    navigate={navigate}
                    userName={user.name}
                    issues={issues}
                    issuesLoaded={issuesLoaded}
                    entryMode={entryMode}
                  />
                )}
              />

              <Route
                path="/topic/:id"
                element={(
                  <TopicChatRoute
                    user={user}
                    issues={issues}
                    issuesLoaded={issuesLoaded}
                    entryMode={entryMode}
                  />
                )}
              />

              <Route path="/kb" element={<KnowledgeBase />} />
              <Route path="/kb/topics/:topicId" element={<KnowledgeBaseTopicPage />} />
              <Route path="/kb/conversations/:viewpointId" element={<KnowledgeBaseConversationPage />} />
              <Route path="/favorites" element={<FavoritesPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/notifications" element={<NotificationPage />} />
              <Route path="/post-questionnaire" element={<PostQuestionnairePage />} />
              <Route path="/debriefing" element={<DebriefingPage />} />
              <Route path="/platform-feedback" element={<PlatformFeedbackPage />} />
              <Route path="/achievement" element={<AchievementPage />} />
              {/* 研究者專用；只能從設定頁的「審核」tab 切換過來（該 tab 只在
                  user.isResearcher 時才顯示），但路徑本身任何登入者都連得到，
                  實際存取控制一律在後端 IsResearcher——一般參與者帳號打開這
                  條路徑只會看到 403 錯誤訊息。 */}
              <Route path="/viewpoint-review" element={<ViewpointReviewPage user={user} />} />
              <Route path="/settings" element={<SettingsPage user={user} />} />
              <Route path="/chat" element={<GodotLobby />} />
            </Routes>
          </div>

          <Sidebar navigate={navigate} isTopicPage={isTopicPage} />
        </div>

        <AchievementToast
          key={unlockedToast?.code}
          achievement={unlockedToast}
          onClose={dismissToast}
          onOpen={() => {
            dismissToast();
            navigate('/achievement');
          }}
        />
      </div>
      </MatchingHeartbeatProvider>
    </NotificationsProvider>
  )
}

export default App
