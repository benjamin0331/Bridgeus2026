import { useState, useEffect, useCallback } from 'react'
import { Routes, Route, useNavigate, useParams, useLocation } from 'react-router-dom'
import './App.css'

import api, { AUTH_LOGOUT_EVENT, clearAuthStorage } from './api/client'
import Navbar from './components/Navbar'
import Sidebar from './components/Sidebar'
import HomePage from './pages/HomePage'
import TopicChat from './pages/TopicChat'
import KnowledgeBase from './pages/KnowledgeBase'
import HistoryPage from './pages/HistoryPage'
import AchievementPage from './pages/AchievementPage'
import { findAchievement } from './pages/achievements.data'
import AchievementToast from './components/AchievementToast'
import LoginPage from './pages/LoginPage'

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
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/achievement" element={<AchievementPage />} />
            <Route path="/chat" element={<div className="empty-page-message">Godot還在排隊</div>} />
          </Routes>
        </div>

        <Sidebar navigate={navigate} isTopicPage={isTopicPage} />
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
