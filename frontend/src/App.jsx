import { useState, useEffect, useCallback } from 'react'
import { Routes, Route, useNavigate, useParams, useLocation } from 'react-router-dom'
import './App.css'

import api, { AUTH_LOGOUT_EVENT, clearAuthStorage } from './api/client'
import Navbar from './components/Navbar'
import Sidebar from './components/Sidebar'
import HomePage from './pages/HomePage'
import TopicChat from './pages/TopicChat'
import KnowledgeBase from './pages/KnowledgeBase'
import KnowledgeBaseTopicPage from './pages/KnowledgeBaseTopicPage'
import KnowledgeBaseConversationPage from './pages/KnowledgeBaseConversationPage'
import HistoryPage from './pages/HistoryPage'
import AchievementPage from './pages/AchievementPage'
import { findAchievement } from './pages/achievements.data'
import AchievementToast from './components/AchievementToast'
import LoginPage from './pages/LoginPage'
import PostQuestionnairePage from './pages/PostQuestionnairePage'
import DebriefingPage from './pages/DebriefingPage'
import PlatformFeedbackPage from './pages/PlatformFeedbackPage'
import ViewpointReviewPage from './pages/ViewpointReviewPage'
import SettingsPage from './pages/SettingsPage'
import GodotLobby from './pages/GodotLobby'

function TopicChatRoute({ user, issues, issuesLoaded }) {
  const { id } = useParams();
  const location = useLocation();

  return (
    <TopicChat
      key={`${id}-${location.search}`}
      user={user}
      issues={issues}
      issuesLoaded={issuesLoaded}
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
  const [entryMode, setEntryMode] = useState('split');

  // 剛進入/刷新時跳出的成就通知。
  // ponytail: 目前偵測未接後端，先預設「一路同行」；之後把這行換成後端回傳的解鎖成就名稱
  const [unlockedToast, setUnlockedToast] = useState(() => findAchievement('一路同行'));

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
        <Route path="*" element={<LoginPage setUser={handleLogin} authMessage={authMessage} />} />
      </Routes>
    );
  }

  const isTopicPage = location.pathname.startsWith('/topic/');

  return (
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
                />
              )}
            />

            <Route path="/kb" element={<KnowledgeBase />} />
            <Route path="/kb/topics/:topicId" element={<KnowledgeBaseTopicPage />} />
            <Route path="/kb/conversations/:viewpointId" element={<KnowledgeBaseConversationPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/post-questionnaire" element={<PostQuestionnairePage />} />
            <Route path="/debriefing" element={<DebriefingPage />} />
            <Route path="/platform-feedback" element={<PlatformFeedbackPage />} />
            <Route path="/achievement" element={<AchievementPage />} />
            {/* 研究者專用；Sidebar 只在 user.isResearcher 時才顯示連結，但路徑本身
                任何登入者都連得到，實際存取控制一律在後端 IsResearcher——
                一般參與者帳號打開這條路徑只會看到 403 錯誤訊息。 */}
            <Route path="/viewpoint-review" element={<ViewpointReviewPage />} />
            <Route path="/settings" element={<SettingsPage user={user} />} />
            <Route path="/chat" element={<GodotLobby />} />
          </Routes>
        </div>

        <Sidebar navigate={navigate} isTopicPage={isTopicPage} isResearcher={Boolean(user?.isResearcher)} />
      </div>

      <AchievementToast
        achievement={unlockedToast}
        onClose={() => setUnlockedToast(null)}
        onOpen={() => {
          setUnlockedToast(null);
          navigate('/achievement');
        }}
      />
    </div>
  )
}

export default App
