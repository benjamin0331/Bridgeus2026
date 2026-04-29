import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import './TopicChat.css';
import SurveyModal from '../components/SurveyModal';
import api from '../api/client';

function mapHistoryToMessages(history, userName) {
  return history.map((message, index) => ({
    id: `${message.role}-${index}`,
    type: message.role === 'agent' ? 'agent' : 'user',
    userName: message.role === 'agent' ? 'BridgeUs' : userName,
    text: message.content,
  }));
}

function TopicChat({ user, issues, issuesLoaded }) {
  const { id } = useParams();

  const [showSurvey, setShowSurvey] = useState(true);
  const [surveyAnswers, setSurveyAnswers] = useState({});
  const [sessionId, setSessionId] = useState(null);
  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState([]);
  const [isSending, setIsSending] = useState(false);
  const [chatError, setChatError] = useState('');
  const [isRightPanelOpen, setIsRightPanelOpen] = useState(false);

  const messagesEndRef = useRef(null);
  const isComposingRef = useRef(false);
  const wsRef = useRef(null);
  const currentAgentMsgIdRef = useRef(null);
  const isSendingRef = useRef(false);

  useEffect(() => {
    isSendingRef.current = isSending;
  }, [isSending]);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);
  const currentIssue = issues?.find((item) => item.id === parseInt(id, 10));
  const displayUserName = user?.name || '公民';

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isSending]);

  const connectWebSocket = (sid) => {
    const token = localStorage.getItem('access');
    const wsBase = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000';
    const ws = new WebSocket(`${wsBase}/ws/dialogue/${sid}/?token=${token}`);

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'agent_stream') {
        setMessages((prev) => {
          const msgId = currentAgentMsgIdRef.current;
          if (msgId && prev.find((m) => m.id === msgId)) {
            return prev.map((m) =>
              m.id === msgId ? { ...m, text: m.text + data.content } : m
            );
          }
          const newId = `agent-${Date.now()}`;
          currentAgentMsgIdRef.current = newId;
          return [...prev, { id: newId, type: 'agent', userName: 'BridgeUs', text: data.content }];
        });
      } else if (data.type === 'agent_stream_end') {
        currentAgentMsgIdRef.current = null;
        setIsSending(false);
      } else if (data.type === 'error') {
        setChatError(data.content);
        setIsSending(false);
      }
    };

    ws.onerror = () => {
      setChatError('WebSocket 連線錯誤，請重新整理頁面。');
      setIsSending(false);
    };

    ws.onclose = () => {
      if (isSendingRef.current) {
        setChatError('連線中斷，請重新整理頁面。');
        setIsSending(false);
      }
    };

    wsRef.current = ws;
    return ws;
  };

  const ensureSession = async () => {
    if (sessionId) {
      return sessionId;
    }

    const response = await api.post('/api/dialogue/sessions/', {
      topic_id: Number(id),
      topic_title: currentIssue?.title || `議題 ${id}`,
      topic_description: currentIssue?.description || '',
      survey_answers: surveyAnswers,
      user_initial_argument: '',
    });

    const sid = response.data.session_id;
    setSessionId(sid);
    return sid;
  };

  const handleSurveySubmit = ({ answers }) => {
    setSurveyAnswers(answers);
    setShowSurvey(false);
    setChatError('');
  };

  const handleSendMessage = async () => {
    const text = inputValue.trim();
    if (!text || isSending) return;

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    setMessages((prev) => [
      ...prev,
      {
        id: `pending-user-${Date.now()}`,
        type: 'user',
        userName: displayUserName,
        text,
      },
    ]);
    setInputValue('');
    setChatError('');
    setIsSending(true);

    try {
      const activeSessionId = await ensureSession();

      const ws = wsRef.current;
      const needsConnect = !ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING;

      if (needsConnect) {
        const newWs = connectWebSocket(activeSessionId);
        await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(new Error('WS 連線逾時')), 5000);
          newWs.onopen = () => { clearTimeout(timeout); resolve(); };
          newWs.onerror = () => { clearTimeout(timeout); reject(new Error('WS 連線失敗')); };
        });
      }

      currentAgentMsgIdRef.current = null;
      wsRef.current.send(JSON.stringify({ type: 'user_message', content: text }));
    } catch (error) {
      const detail = error?.message || '無法連接對話服務，請稍後再試。';
      setChatError(detail);
      setIsSending(false);
    }
  };

  const textareaRef = useRef(null);

  const handleTextareaChange = (e) => {
    setInputValue(e.target.value);
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  };

  const handleInputKeyDown = (event) => {
    if (event.key !== 'Enter') {
      return;
    }

    if (event.nativeEvent.isComposing || isComposingRef.current) {
      return;
    }

    if (event.shiftKey) {
      return;
    }

    event.preventDefault();
    handleSendMessage();
  };

  if (!issuesLoaded) {
    return <div style={{ padding: '50px', textAlign: 'center' }}>正在載入議題數據...</div>;
  }

  if (!currentIssue) {
    return <div style={{ padding: '50px', textAlign: 'center' }}>找不到這個議題，請返回首頁重新選擇。</div>;
  }

  return (
    <div className="chat-page-container">
      {showSurvey && (
        <SurveyModal isOpen={showSurvey} onSubmit={handleSurveySubmit} />
      )}

      {/* 手機版：右側功能面板展開按鈕 */}
      {!isRightPanelOpen && (
        <button
          className="panel-toggle-btn"
          onClick={() => setIsRightPanelOpen(true)}
          aria-label="展開功能面板"
        >
          ⊞
        </button>
      )}

      {/* 手機版：點擊遮罩關閉面板 */}
      {isRightPanelOpen && (
        <div className="panel-backdrop" onClick={() => setIsRightPanelOpen(false)} />
      )}

      <div className={`chat-left-interaction-area ${!showSurvey ? 'content-visible' : ''}`}>
        <div className="current-topic-section">
          <h1 className="topic-title">{currentIssue?.title || '未知的領域'}</h1>
        </div>

        <div className="chat-messages-display">
          {messages.map((msg) => (
            <div key={msg.id} className={`message-row ${msg.type === 'user' ? 'user-message' : ''}`}>
              <div className="message-user-info">
                <img src="/icon.jpg" alt="Avatar" className="message-avatar" />
                <span className="message-username">{msg.userName}</span>
              </div>
              <div className="message-bubble">{msg.text}</div>
            </div>
          ))}
          {isSending && (
            <div className="message-row">
              <div className="message-user-info">
                <img src="/icon.jpg" alt="Avatar" className="message-avatar" />
                <span className="message-username">BridgeUs</span>
              </div>
              <div className="message-bubble">正在整理回應...</div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="chat-input-area">
          <div className="chat-input-wrapper">
            <textarea
              ref={textareaRef}
              className="chat-text-input"
              placeholder={
                showSurvey
                  ? '請先完成立場檢測問卷'
                  : '輸入觀點... '
              }
              value={inputValue}
              disabled={showSurvey || isSending}
              onChange={handleTextareaChange}
              onCompositionStart={() => {
                isComposingRef.current = true;
              }}
              onCompositionEnd={() => {
                isComposingRef.current = false;
              }}
              onKeyDown={handleInputKeyDown}
              rows={1}
            />
            <button
              className="chat-send-btn"
              onClick={handleSendMessage}
              disabled={showSurvey || isSending || !inputValue.trim()}
              aria-label="發送訊息"
            >
              <img src="/arrow-right.png" alt="發送" className="send-icon" />
            </button>
          </div>
          {chatError && (
            <p style={{ color: '#b42318', fontSize: '14px', marginTop: '8px' }}>
              {chatError}
            </p>
          )}
        </div>
      </div>

      <aside className={`chat-right-function-area${isRightPanelOpen ? ' panel-open' : ''}`}>
        <div className="right-major-feature-box"></div>

        <div className="right-minor-feature-row">
          <div className="minor-feature-box"></div>
          <div className="minor-feature-box"></div>
          <div className="minor-feature-box"></div>
        </div>
      </aside>
    </div>
  );
}

export default TopicChat;
