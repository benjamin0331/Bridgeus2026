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

function TopicChat({ user, issues }) {
  const { id } = useParams();

  const [showSurvey, setShowSurvey] = useState(true);
  const [surveyAnswers, setSurveyAnswers] = useState({});
  const [sessionId, setSessionId] = useState(null);
  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState([]);
  const [isSending, setIsSending] = useState(false);
  const [chatError, setChatError] = useState('');

  const messagesEndRef = useRef(null);
  const isComposingRef = useRef(false);
  const currentIssue = issues?.find((item) => item.id === parseInt(id, 10));
  const displayUserName = user?.name || '公民';

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isSending]);

  const ensureSession = async () => {
    if (sessionId) {
      return sessionId;
    }

    const response = await api.post('/api/dialogue/sessions/', {
      topic_id: Number(id),
      topic_title: currentIssue?.title || `議題 ${id}`,
      topic_description: currentIssue?.title || '',
      survey_answers: surveyAnswers,
      user_initial_argument: '',
    });

    setSessionId(response.data.session_id);
    return response.data.session_id;
  };

  const handleSurveySubmit = ({ answers }) => {
    setSurveyAnswers(answers);
    setShowSurvey(false);
    setChatError('');
  };

  const handleSendMessage = async () => {
    const text = inputValue.trim();
    if (!text || isSending) return;

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
      const response = await api.post(
        `/api/dialogue/sessions/${activeSessionId}/reply/`,
        { message: text },
      );

      setMessages(mapHistoryToMessages(response.data.history, displayUserName));
    } catch (error) {
      const detail =
        error?.response?.data?.detail ||
        '目前無法取得對話回覆，請稍後再試。';

      setChatError(detail);
      setMessages((prev) => [
        ...prev,
        {
          id: `system-error-${Date.now()}`,
          type: 'agent',
          userName: '系統',
          text: detail,
        },
      ]);
    } finally {
      setIsSending(false);
    }
  };

  const handleInputKeyDown = (event) => {
    if (event.key !== 'Enter') {
      return;
    }

    if (event.nativeEvent.isComposing || isComposingRef.current) {
      return;
    }

    event.preventDefault();
    handleSendMessage();
  };

  if (!issues || issues.length === 0) {
    return <div style={{ padding: '50px', textAlign: 'center' }}>正在載入議題數據...</div>;
  }

  return (
    <div className="chat-page-container">
      {showSurvey && (
        <SurveyModal isOpen={showSurvey} onSubmit={handleSurveySubmit} />
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
          <input
            type="text"
            className="chat-text-input"
            placeholder={
              showSurvey
                ? '請先完成立場檢測問卷'
                : '輸入觀點... 按下 Enter 發送'
            }
            value={inputValue}
            disabled={showSurvey || isSending}
            onChange={(e) => setInputValue(e.target.value)}
            onCompositionStart={() => {
              isComposingRef.current = true;
            }}
            onCompositionEnd={() => {
              isComposingRef.current = false;
            }}
            onKeyDown={handleInputKeyDown}
          />
          {chatError && (
            <p style={{ color: '#b42318', fontSize: '14px', marginTop: '8px' }}>
              {chatError}
            </p>
          )}
        </div>
      </div>

      <aside className="chat-right-function-area">
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
