import { useState, useEffect, useCallback } from 'react'
import { Routes, Route, useNavigate, useParams, useLocation } from 'react-router-dom'
import './App.css'

import api, { AUTH_LOGOUT_EVENT, clearAuthStorage, getAccessTokenExpiry } from './api/client'
import Navbar from './components/Navbar'
import Sidebar from './components/Sidebar'
import HomePage from './pages/HomePage'
import TopicChat from './pages/TopicChat'
import KnowledgeBase from './pages/KnowledgeBase'
import LoginPage from './pages/LoginPage'

function TopicChatRoute({ user, issues, issuesLoaded }) {
  const { id } = useParams();

  return <TopicChat key={id} user={user} issues={issues} issuesLoaded={issuesLoaded} />;
}

function App() {
  const navigate = useNavigate();
  const location = useLocation();

  // 使用者登入狀態，初始值優先從 localStorage 還原
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
  // 議題資料列表
  const [issues, setIssues] = useState([]);
  const [issuesLoaded, setIssuesLoaded] = useState(false);

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

    const expiresAt = getAccessTokenExpiry();
    if (!expiresAt) {
      return undefined;
    }

    const timerId = window.setTimeout(() => {
      handleLogout('登入已過期，請重新登入。');
    }, Math.max(expiresAt - Date.now(), 0));

    return () => window.clearTimeout(timerId);
  }, [handleLogout, user]);

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
        console.error("Data fetching error:", error);
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

  // 權限驗證邏輯：若使用者未登入，強制導向並僅顯示登入頁面
  if (!user) {
    return (
      <Routes>
        {/* 使用萬用路徑，確保未驗證使用者無法存取內部路由 */}
        <Route path="*" element={<LoginPage setUser={handleLogin} authMessage={authMessage} />} />
      </Routes>
    );
  }

  const isTopicPage = location.pathname.startsWith('/topic/');

  // 驗證成功後顯示系統主架構
  return (
    <div className="app-container">
      {/* 導航欄：傳遞 navigate 方法與使用者 ID */}
      <Navbar navigate={navigate} userId={user.id} onLogout={handleLogout} hideLogout={isTopicPage} />
      
      <div className="main-layout-wrapper">
        <div className="content-area">
          <Routes>
            {/* 系統主要路由配置 */}
            <Route
              path="/"
              element={
                <HomePage
                  navigate={navigate}
                  userName={user.name}
                  issues={issues}
                  issuesLoaded={issuesLoaded}
                />
              }
            />
            
            {/* 議題對話頁面：根據動態 ID 顯示內容 */}
            <Route
              path="/topic/:id"
              element={
                <TopicChatRoute
                  user={user}
                  issues={issues}
                  issuesLoaded={issuesLoaded}
                />
              }
            />
            
            {/* 觀點知識庫頁面 */}
            <Route path="/kb" element={<KnowledgeBase />} />
            
            {/* 虛擬大廳預留位置 */}
            <Route path="/chat" element={<div className="empty-page-message">Godot還在排隊</div>} />
          </Routes>
        </div>
        
        {/* 側邊欄導航 */}
        <Sidebar navigate={navigate} isTopicPage={isTopicPage} />
      </div>
    </div>
  )
}

export default App
