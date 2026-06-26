/* eslint-disable react-hooks/set-state-in-effect */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import './TopicChat.css';
import ConversationTreePanel from '../components/ConversationTreePanel';
import SurveyModal from '../components/SurveyModal';
import api from '../api/client';

const MATCHING_POLL_INTERVAL_MS = 3000;
const MATCH_SCROLL_BOTTOM_THRESHOLD_PX = 96;
const MATCH_SELF_NAME = '我';
const MATCH_PARTNER_NAME = '匿名對話者';

function getWebSocketBaseUrl() {
  const configuredBase = import.meta.env.VITE_WS_BASE_URL;
  if (configuredBase) {
    return configuredBase.replace(/\/$/, '');
  }

  const apiBase = import.meta.env.VITE_API_BASE_URL || '';
  if (apiBase.startsWith('http://') || apiBase.startsWith('https://')) {
    return apiBase.replace(/^http/, 'ws').replace(/\/$/, '');
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${apiBase}`.replace(/\/$/, '');
}

function buildDialogueWebSocketUrl(sessionId, token) {
  const query = token ? `?token=${encodeURIComponent(token)}` : '';
  return `${getWebSocketBaseUrl()}/ws/dialogue/${sessionId}/${query}`;
}

function buildMatchWebSocketUrl(roomId, token) {
  const query = token ? `?token=${encodeURIComponent(token)}` : '';
  return `${getWebSocketBaseUrl()}/ws/matching/rooms/${roomId}/${query}`;
}

function waitForSocketOpen(socket) {
  if (socket.readyState === WebSocket.OPEN) {
    return Promise.resolve();
  }

  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error('WebSocket 連線逾時'));
    }, 5000);

    const cleanup = () => {
      window.clearTimeout(timeout);
      socket.removeEventListener('open', handleOpen);
      socket.removeEventListener('error', handleError);
    };

    const handleOpen = () => {
      cleanup();
      resolve();
    };

    const handleError = () => {
      cleanup();
      reject(new Error('WebSocket 連線失敗'));
    };

    socket.addEventListener('open', handleOpen);
    socket.addEventListener('error', handleError);
  });
}

function isElementNearBottom(element) {
  if (!element) {
    return true;
  }

  const distanceFromBottom =
    element.scrollHeight - element.scrollTop - element.clientHeight;
  return distanceFromBottom <= MATCH_SCROLL_BOTTOM_THRESHOLD_PX;
}

function mapHistoryToMessages(history, userName) {
  return history.map((message, index) => ({
    id: `${message.role}-${index}`,
    type: message.role === 'agent' ? 'agent' : 'user',
    userName: message.role === 'agent' ? 'BridgeUs' : userName,
    text: message.content,
  }));
}

function formatTimestamp(value) {
  if (!value) {
    return '尚未建立時間';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '時間格式異常';
  }

  return new Intl.DateTimeFormat('zh-TW', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function formatStanceCategory(category) {
  if (category === 'support') {
    return '較支持本議題';
  }

  if (category === 'oppose') {
    return '較反對本議題';
  }

  if (category === 'neutral') {
    return '立場中立或尚未明確';
  }

  return '尚未建立立場標記';
}

function formatSuggestionCategory(category) {
  if (category === 'rephrase') {
    return 'AI 改寫建議';
  }

  if (category === 'redirect') {
    return '回到主題提醒';
  }

  if (category === 'direction') {
    return '討論方向建議';
  }

  return 'AI 對話提示';
}

function formatDriftValue(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return '尚未計算';
  }

  return numericValue.toFixed(4);
}

function formatStanceScoreValue(value) {
  if (value === null || value === undefined || value === '') {
    return '尚未建立';
  }

  const numericValue = Number(value);
  if (Number.isFinite(numericValue)) {
    return numericValue.toFixed(2);
  }

  return value || '尚未建立';
}

function extractAiStanceMeta(payload) {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  return {
    stanceScore: payload.stance_score ?? null,
    stanceCategory: payload.stance_category || null,
    stanceLabel: payload.stance_label || '',
  };
}

function extractStanceDrift(payload) {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  return payload.stance_drift || null;
}

function mapMatchMessagesToDisplay(messages, userId) {
  const currentUserId = Number(userId);

  return messages.map((message) => {
    const senderId = Number(message.sender_id);
    const isCurrentUser = Number.isFinite(currentUserId) && senderId === currentUserId;

    return {
      id: `match-message-${message.id}`,
      type: isCurrentUser ? 'user' : 'agent',
      userName: isCurrentUser ? MATCH_SELF_NAME : MATCH_PARTNER_NAME,
      text: message.content,
      timestamp: message.created_at,
    };
  });
}

function TopicChat({ user, issues, issuesLoaded }) {
  const { id } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const mode = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return params.get('mode') === 'match' ? 'match' : 'ai';
  }, [location.search]);
  const isMatchingMode = mode === 'match';
  const modeLabel = isMatchingMode ? '配對模式' : 'AI 模式';

  const [showSurvey, setShowSurvey] = useState(!isMatchingMode);
  const [survey, setSurvey] = useState(null);
  const [surveyAnswers, setSurveyAnswers] = useState({});
  const [surveyOpenAnswers, setSurveyOpenAnswers] = useState({});
  const [isSurveyLoading, setIsSurveyLoading] = useState(true);
  const [surveyError, setSurveyError] = useState('');

  const [sessionId, setSessionId] = useState(null);
  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState([]);
  const [aiStanceMeta, setAiStanceMeta] = useState(null);
  const [aiStanceDrift, setAiStanceDrift] = useState(null);
  const [isSending, setIsSending] = useState(false);
  const [isAgentStreaming, setIsAgentStreaming] = useState(false);
  const [chatError, setChatError] = useState('');
  const [isSessionRestoring, setIsSessionRestoring] = useState(false);
  const [pendingRestoredSession, setPendingRestoredSession] = useState(null);

  const [matchingState, setMatchingState] = useState(null);
  const [isMatchingStateLoading, setIsMatchingStateLoading] = useState(isMatchingMode);
  const [isMatchingActionLoading, setIsMatchingActionLoading] = useState(false);
  const [matchingError, setMatchingError] = useState('');
  const [matchMessages, setMatchMessages] = useState([]);
  const [matchStanceDrift, setMatchStanceDrift] = useState(null);
  const [semanticTreePayload, setSemanticTreePayload] = useState(null);
  const [semanticTreeStatus, setSemanticTreeStatus] = useState('ready');
  const [semanticTreeMessage, setSemanticTreeMessage] = useState('');
  const [isSemanticTreeLoading, setIsSemanticTreeLoading] = useState(false);
  const [isSemanticTreeAnalyzing, setIsSemanticTreeAnalyzing] = useState(false);
  const [isMatchMessagesLoading, setIsMatchMessagesLoading] = useState(false);
  const [isMatchSending, setIsMatchSending] = useState(false);
  const [matchChatError, setMatchChatError] = useState('');
  const [matchAssistNotice, setMatchAssistNotice] = useState(null);
  const [pendingMatchSuggestion, setPendingMatchSuggestion] = useState(null);
  const [matchSuggestionDraft, setMatchSuggestionDraft] = useState(null);
  const [showScrollToBottomButton, setShowScrollToBottomButton] = useState(false);

  const [isRightPanelOpen, setIsRightPanelOpen] = useState(false);

  const messagesContainerRef = useRef(null);
  const messagesEndRef = useRef(null);
  const isComposingRef = useRef(false);
  const textareaRef = useRef(null);
  const wsRef = useRef(null);
  const wsSessionIdRef = useRef(null);
  const matchWsRef = useRef(null);
  const matchWsRoomIdRef = useRef(null);
  const currentAgentMsgIdRef = useRef(null);
  const isSendingRef = useRef(false);
  const activeMatchRef = useRef({ roomId: null, status: null, topicId: null });
  const leaveRequestSentRef = useRef(false);
  const cancelQueueRequestSentRef = useRef(false);
  const isChatPageMountedRef = useRef(true);
  const shouldAutoScrollMatchRef = useRef(true);
  const semanticTreeAnalyzeSignatureRef = useRef('');
  const currentIssue = issues?.find((item) => item.id === parseInt(id, 10));
  const displayUserName = user?.name || '公民';
  const matchPartnerName = MATCH_PARTNER_NAME;
  const isMatchChatReady = Boolean(
    isMatchingMode &&
      !showSurvey &&
      matchingState?.status === 'matched' &&
      matchingState?.room_id,
  );
  const matchMessageIdsSignature = useMemo(
    () => matchMessages.map((message) => message.id).filter(Boolean).join(','),
    [matchMessages],
  );
  const aiUserMessageIdsSignature = useMemo(
    () => messages
      .filter((message) => message.type === 'user')
      .map((message) => message.id)
      .filter(Boolean)
      .join(','),
    [messages],
  );
  const pendingRestoredUserMessageCount = useMemo(() => {
    const history = pendingRestoredSession?.history;
    if (!Array.isArray(history)) {
      return 0;
    }
    return history.filter((message) => message.role === 'user').length;
  }, [pendingRestoredSession]);
  const pendingRestoredPreview = useMemo(() => {
    const history = pendingRestoredSession?.history;
    if (!Array.isArray(history) || history.length === 0) {
      return '';
    }
    const latestMessage = history[history.length - 1];
    return latestMessage?.content || '';
  }, [pendingRestoredSession]);

  const scrollMessagesToBottom = useCallback((behavior = 'smooth') => {
    window.requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior, block: 'end' });
    });
  }, []);

  const focusChatInput = useCallback(() => {
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus({ preventScroll: true });
    });
  }, []);

  const resetAiSemanticTreeState = useCallback(() => {
    setSemanticTreePayload(null);
    setSemanticTreeStatus('ready');
    setSemanticTreeMessage('');
    setIsSemanticTreeLoading(false);
    setIsSemanticTreeAnalyzing(false);
    semanticTreeAnalyzeSignatureRef.current = '';
  }, []);

  const handleContinueRestoredSession = useCallback(() => {
    if (!pendingRestoredSession?.session_id) {
      return;
    }

    wsRef.current?.close();
    wsRef.current = null;
    wsSessionIdRef.current = null;
    currentAgentMsgIdRef.current = null;
    resetAiSemanticTreeState();
    setSessionId(pendingRestoredSession.session_id);
    setMessages(mapHistoryToMessages(pendingRestoredSession.history || [], displayUserName));
    setAiStanceMeta(extractAiStanceMeta(pendingRestoredSession));
    setAiStanceDrift(extractStanceDrift(pendingRestoredSession));
    setPendingRestoredSession(null);
    setShowSurvey(false);
    setChatError('');
    window.requestAnimationFrame(() => {
      scrollMessagesToBottom('auto');
    });
  }, [
    displayUserName,
    pendingRestoredSession,
    resetAiSemanticTreeState,
    scrollMessagesToBottom,
  ]);

  const handleStartNewAiDialogue = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    wsSessionIdRef.current = null;
    currentAgentMsgIdRef.current = null;
    resetAiSemanticTreeState();
    setPendingRestoredSession(null);
    setSessionId(null);
    setMessages([]);
    setAiStanceMeta(null);
    setAiStanceDrift(null);
    setSurveyAnswers({});
    setSurveyOpenAnswers({});
    setInputValue('');
    setChatError('');
    setIsSending(false);
    setIsAgentStreaming(false);
    setShowSurvey(true);
  }, [resetAiSemanticTreeState]);

  const applySemanticTreePayload = useCallback((payload) => {
    setSemanticTreePayload(payload || null);
    setSemanticTreeStatus(payload?.analysisStatus || 'ready');
    setSemanticTreeMessage(payload?.message || '');
  }, []);

  const appendMatchMessage = useCallback((message) => {
    if (!message?.id) {
      return;
    }

    setMatchMessages((prev) => {
      const messageId = Number(message.id);
      if (prev.some((existing) => Number(existing.id) === messageId)) {
        return prev;
      }

      return [...prev, message];
    });
  }, []);

  const handleMessagesScroll = useCallback((event) => {
    if (!isMatchingMode) {
      return;
    }

    const isNearBottom = isElementNearBottom(event.currentTarget);
    shouldAutoScrollMatchRef.current = isNearBottom;
    setShowScrollToBottomButton(isMatchChatReady && !isNearBottom);
  }, [isMatchChatReady, isMatchingMode]);

  const handleScrollToBottom = useCallback(() => {
    shouldAutoScrollMatchRef.current = true;
    setShowScrollToBottomButton(false);
    scrollMessagesToBottom('smooth');
    focusChatInput();
  }, [focusChatInput, scrollMessagesToBottom]);

  const closeMatchWebSocket = useCallback((socket = matchWsRef.current) => {
    if (!socket) {
      return;
    }

    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;

    if (socket.readyState !== WebSocket.CLOSED) {
      socket.close();
    }

    if (matchWsRef.current === socket) {
      matchWsRef.current = null;
      matchWsRoomIdRef.current = null;
    }
  }, []);

  const connectMatchWebSocket = useCallback((roomId) => {
    const token = localStorage.getItem('access');
    if (!token) {
      setMatchChatError('登入已過期，請重新登入。');
      return null;
    }

    const socket = new WebSocket(buildMatchWebSocketUrl(roomId, token));
    matchWsRef.current = socket;
    matchWsRoomIdRef.current = roomId;

    socket.onmessage = (event) => {
      let data = null;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      if (data.type === 'match_message') {
        appendMatchMessage(data.message);
        setMatchChatError('');
        return;
      }

      if (data.type === 'match_system_prompt') {
        setMatchAssistNotice({
          id: `match-system-${Date.now()}`,
          category: data.category || 'system',
          message: data.message || '系統提醒',
        });
        setMatchChatError('');
        return;
      }

      if (data.type === 'match_ai_suggestion') {
        setPendingMatchSuggestion(data);
        setMatchSuggestionDraft(null);
        setMatchAssistNotice(null);
        setMatchChatError('');
        return;
      }

      if (data.type === 'error') {
        setMatchChatError(data.content || '配對聊天室連線發生錯誤。');
      }
    };

    socket.onerror = () => {
      setMatchChatError('配對聊天室 WebSocket 連線錯誤，會暫時改用 HTTP 備援。');
    };

    socket.onclose = () => {
      if (matchWsRef.current === socket) {
        matchWsRef.current = null;
        matchWsRoomIdRef.current = null;
      }
    };

    return socket;
  }, [appendMatchMessage]);

  useEffect(() => {
    isSendingRef.current = isSending;
  }, [isSending]);

  useEffect(() => {
    isChatPageMountedRef.current = true;

    return () => {
      isChatPageMountedRef.current = false;
      wsRef.current?.close();
      wsRef.current = null;
      wsSessionIdRef.current = null;
      closeMatchWebSocket();
    };
  }, [closeMatchWebSocket]);

  useEffect(() => {
    if (isMatchingMode) {
      return;
    }

    scrollMessagesToBottom('smooth');
  }, [isAgentStreaming, isMatchingMode, isSending, messages, scrollMessagesToBottom]);

  useEffect(() => {
    if (!isMatchingMode) {
      setShowScrollToBottomButton(false);
      return;
    }

    if (!isMatchChatReady) {
      shouldAutoScrollMatchRef.current = true;
      setShowScrollToBottomButton(false);
      return;
    }

    if (shouldAutoScrollMatchRef.current) {
      scrollMessagesToBottom('smooth');
      setShowScrollToBottomButton(false);
    } else {
      setShowScrollToBottomButton(true);
    }
  }, [
    isMatchChatReady,
    isMatchSending,
    isMatchingMode,
    matchMessages,
    scrollMessagesToBottom,
  ]);

  useEffect(() => {
    activeMatchRef.current = {
      roomId: showSurvey ? null : matchingState?.room_id || null,
      status: showSurvey ? null : matchingState?.status || null,
      topicId: Number(id),
    };

    if (matchingState?.status !== 'matched') {
      leaveRequestSentRef.current = false;
    }
    if (matchingState?.status !== 'matching') {
      cancelQueueRequestSentRef.current = false;
    }
  }, [id, matchingState?.room_id, matchingState?.status, showSurvey]);

  useEffect(() => {
    setSurvey(null);
    setSurveyAnswers({});
    setSurveyOpenAnswers({});
    setSurveyError('');
    setShowSurvey(!isMatchingMode);
    setSessionId(null);
    setInputValue('');
    setMessages([]);
    setAiStanceMeta(null);
    setAiStanceDrift(null);
    setIsSending(false);
    setChatError('');
    setIsAgentStreaming(false);
    setIsSessionRestoring(false);
    setPendingRestoredSession(null);
    currentAgentMsgIdRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    wsSessionIdRef.current = null;
    closeMatchWebSocket();
    setMatchingState(null);
    setIsMatchingStateLoading(isMatchingMode);
    setIsMatchingActionLoading(false);
    setMatchingError('');
    setMatchMessages([]);
    setMatchStanceDrift(null);
    setSemanticTreePayload(null);
    setSemanticTreeStatus('ready');
    setSemanticTreeMessage('');
    setIsSemanticTreeLoading(false);
    setIsSemanticTreeAnalyzing(false);
    semanticTreeAnalyzeSignatureRef.current = '';
    setIsMatchMessagesLoading(false);
    setIsMatchSending(false);
    setMatchChatError('');
    setMatchAssistNotice(null);
    setPendingMatchSuggestion(null);
    setMatchSuggestionDraft(null);
    activeMatchRef.current = { roomId: null, status: null, topicId: null };
    leaveRequestSentRef.current = false;
    cancelQueueRequestSentRef.current = false;
    shouldAutoScrollMatchRef.current = true;
    setShowScrollToBottomButton(false);
  }, [closeMatchWebSocket, id, isMatchingMode]);

  useEffect(() => {
    if (!currentIssue) {
      setSurvey(null);
      setSurveyAnswers({});
      setSurveyOpenAnswers({});
      setIsSurveyLoading(false);
      setShowSurvey(false);
      return undefined;
    }

    let cancelled = false;

    const fetchSurvey = async () => {
      setIsSurveyLoading(true);
      setSurveyError('');

      try {
        const response = await api.get(`/api/dialogue/topics/${id}/survey/`);
        if (!cancelled) {
          setSurvey(response.data);
        }
      } catch (error) {
        const detail =
          error?.response?.data?.detail ||
          '目前無法載入問卷，請稍後再試。';

        if (!cancelled) {
          setSurvey(null);
          setSurveyError(detail);
        }
      } finally {
        if (!cancelled) {
          setIsSurveyLoading(false);
        }
      }
    };

    fetchSurvey();

    return () => {
      cancelled = true;
    };
  }, [currentIssue, id]);

  useEffect(() => {
    if (!isMatchingMode || !currentIssue) {
      return undefined;
    }

    let cancelled = false;

    const fetchMatchingState = async () => {
      setIsMatchingStateLoading(true);
      setMatchingError('');

      try {
        const response = await api.get(`/api/matching/status/?topic_id=${id}`);
        if (!cancelled) {
          setMatchingState(response.data);
          setShowSurvey(response.data.status === 'idle');
        }
      } catch (error) {
        if (!cancelled) {
          setMatchingState(null);
          setShowSurvey(true);
          setMatchingError(
            error?.response?.data?.detail ||
              '目前無法同步配對狀態，請稍後再試。',
          );
        }
      } finally {
        if (!cancelled) {
          setIsMatchingStateLoading(false);
        }
      }
    };

    fetchMatchingState();

    return () => {
      cancelled = true;
    };
  }, [currentIssue, id, isMatchingMode]);

  useEffect(() => {
    if (isMatchingMode || !currentIssue) {
      return undefined;
    }

    let cancelled = false;

    const restoreLatestSession = async () => {
      setIsSessionRestoring(true);
      setChatError('');
      setShowSurvey(false);

      try {
        const response = await api.get(`/api/dialogue/sessions/latest/?topic_id=${id}`);
        if (cancelled) {
          return;
        }

        const restoredHistory = Array.isArray(response.data?.history)
          ? response.data.history
          : [];
        setPendingRestoredSession({
          ...response.data,
          history: restoredHistory,
        });
        setSessionId(null);
        setMessages([]);
        setAiStanceMeta(null);
        setAiStanceDrift(null);
        setShowSurvey(false);
      } catch (error) {
        if (cancelled) {
          return;
        }

        setSessionId(null);
        setMessages([]);
        setAiStanceMeta(null);
        setAiStanceDrift(null);
        setPendingRestoredSession(null);
        setShowSurvey(true);
        if (error?.response?.status && error.response.status !== 404) {
          setChatError(
            error?.response?.data?.detail ||
              '目前無法恢復最近一次 AI 對話，請稍後再試。',
          );
        }
      } finally {
        if (!cancelled) {
          setIsSessionRestoring(false);
        }
      }
    };

    void restoreLatestSession();

    return () => {
      cancelled = true;
    };
  }, [currentIssue, displayUserName, id, isMatchingMode]);

  useEffect(() => {
    if (!isMatchChatReady || !matchingState?.room_id) {
      closeMatchWebSocket();
      if (showSurvey) {
        setMatchChatError('');
      }
      return undefined;
    }

    if (
      matchWsRef.current &&
      matchWsRoomIdRef.current === matchingState.room_id &&
      matchWsRef.current.readyState !== WebSocket.CLOSED &&
      matchWsRef.current.readyState !== WebSocket.CLOSING
    ) {
      return undefined;
    }

    closeMatchWebSocket();
    const socket = connectMatchWebSocket(matchingState.room_id);

    return () => {
      if (matchWsRef.current === socket) {
        matchWsRef.current = null;
        matchWsRoomIdRef.current = null;
      }
      closeMatchWebSocket(socket);
    };
  }, [
    closeMatchWebSocket,
    connectMatchWebSocket,
    isMatchChatReady,
    matchingState?.room_id,
    showSurvey,
  ]);

  useEffect(() => {
    if (!isMatchingMode || matchingState?.status !== 'matching') {
      return undefined;
    }

    let cancelled = false;

    const pollTimer = window.setInterval(async () => {
      try {
        const response = await api.get(`/api/matching/status/?topic_id=${id}`);
        if (cancelled) {
          return;
        }

        setMatchingState(response.data);
        if (response.data.status !== 'matching') {
          setMatchingError('');
        }
      } catch (error) {
        if (cancelled) {
          return;
        }

        setMatchingError(
          error?.response?.data?.detail ||
            '目前無法同步配對狀態，請稍後再試。',
        );
      }
    }, MATCHING_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(pollTimer);
    };
  }, [id, isMatchingMode, matchingState?.status]);

  useEffect(() => {
    if (!isMatchChatReady) {
      return undefined;
    }

    let cancelled = false;

    const applyRoomPayload = (payload) => {
      setMatchMessages(Array.isArray(payload?.messages) ? payload.messages : []);
      setMatchStanceDrift(payload?.stance_drift || null);
      setMatchingState((prev) => {
        if (!prev) {
          return prev;
        }

        return {
          ...prev,
          status: payload?.status ?? prev.status,
          room_id: payload?.room_id ?? prev.room_id,
          match_id: payload?.match_id ?? prev.match_id,
          other_user_id: payload?.other_user_id ?? prev.other_user_id,
          other_user_name: payload?.other_user_name ?? prev.other_user_name,
          closed_at: payload?.closed_at ?? prev.closed_at,
        };
      });
    };

    const fetchRoomMessages = async ({ silent = false } = {}) => {
      if (!silent) {
        setIsMatchMessagesLoading(true);
      }

      try {
        const response = await api.get(
          `/api/matching/rooms/${matchingState.room_id}/messages/`,
        );
        if (cancelled) {
          return;
        }

        applyRoomPayload(response.data);
        setMatchChatError('');
      } catch (error) {
        if (cancelled) {
          return;
        }

        setMatchChatError(
          error?.response?.data?.detail ||
            '目前無法載入配對聊天室訊息，請稍後再試。',
        );
      } finally {
        if (!cancelled && !silent) {
          setIsMatchMessagesLoading(false);
        }
      }
    };

    fetchRoomMessages();
    const pollTimer = window.setInterval(() => {
      void fetchRoomMessages({ silent: true });
    }, MATCHING_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(pollTimer);
    };
  }, [isMatchChatReady, isMatchingMode, matchingState?.room_id]);

  useEffect(() => {
    if (!isMatchingMode) {
      return undefined;
    }

    if (!isMatchChatReady || !matchingState?.room_id) {
      setSemanticTreePayload(null);
      setSemanticTreeStatus('ready');
      setSemanticTreeMessage('');
      setIsSemanticTreeLoading(false);
      setIsSemanticTreeAnalyzing(false);
      semanticTreeAnalyzeSignatureRef.current = '';
      return undefined;
    }

    let cancelled = false;
    const roomId = matchingState.room_id;

    const fetchSemanticTree = async () => {
      setIsSemanticTreeLoading(true);
      setSemanticTreeStatus('ready');
      setSemanticTreeMessage('');

      try {
        const response = await api.get(`/api/matching/rooms/${roomId}/semantic-tree/`);
        if (!cancelled) {
          applySemanticTreePayload(response.data);
        }
      } catch (error) {
        if (!cancelled) {
          setSemanticTreeStatus(error?.response?.data?.analysisStatus || 'load_failed');
          setSemanticTreeMessage(
            error?.response?.data?.message ||
              error?.response?.data?.detail ||
              '目前無法載入語意樹。',
          );
        }
      } finally {
        if (!cancelled) {
          setIsSemanticTreeLoading(false);
        }
      }
    };

    semanticTreeAnalyzeSignatureRef.current = '';
    void fetchSemanticTree();

    return () => {
      cancelled = true;
    };
  }, [applySemanticTreePayload, isMatchChatReady, isMatchingMode, matchingState?.room_id]);

  useEffect(() => {
    if (isMatchingMode) {
      return undefined;
    }

    if (!sessionId) {
      setSemanticTreePayload(null);
      setSemanticTreeStatus('ready');
      setSemanticTreeMessage('');
      setIsSemanticTreeLoading(false);
      setIsSemanticTreeAnalyzing(false);
      semanticTreeAnalyzeSignatureRef.current = '';
      return undefined;
    }

    let cancelled = false;

    const fetchSemanticTree = async () => {
      setIsSemanticTreeLoading(true);
      setSemanticTreeStatus('ready');
      setSemanticTreeMessage('');

      try {
        const response = await api.get(`/api/dialogue/sessions/${sessionId}/semantic-tree/`);
        if (!cancelled) {
          applySemanticTreePayload(response.data);
        }
      } catch (error) {
        if (!cancelled) {
          setSemanticTreeStatus(error?.response?.data?.analysisStatus || 'load_failed');
          setSemanticTreeMessage(
            error?.response?.data?.message ||
              error?.response?.data?.detail ||
              '目前無法載入語意樹。',
          );
        }
      } finally {
        if (!cancelled) {
          setIsSemanticTreeLoading(false);
        }
      }
    };

    semanticTreeAnalyzeSignatureRef.current = '';
    void fetchSemanticTree();

    return () => {
      cancelled = true;
    };
  }, [applySemanticTreePayload, isMatchingMode, sessionId]);

  useEffect(() => {
    if (!isMatchingMode) {
      return undefined;
    }

    if (!isMatchChatReady || !matchingState?.room_id || !matchMessageIdsSignature) {
      return undefined;
    }

    const roomId = matchingState.room_id;
    const signature = `${roomId}:${matchMessageIdsSignature}`;
    if (semanticTreeAnalyzeSignatureRef.current === signature) {
      return undefined;
    }

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setIsSemanticTreeAnalyzing(true);

      try {
        const response = await api.post(`/api/matching/rooms/${roomId}/semantic-tree/analyze/`);
        if (!cancelled) {
          applySemanticTreePayload(response.data);
          semanticTreeAnalyzeSignatureRef.current = signature;
        }
      } catch (error) {
        if (!cancelled) {
          setSemanticTreeStatus(error?.response?.data?.analysisStatus || 'analyze_failed');
          setSemanticTreeMessage(
            error?.response?.data?.message ||
              error?.response?.data?.detail ||
              '語意脈絡分析暫時失敗，聊天室仍可使用。',
          );
          semanticTreeAnalyzeSignatureRef.current = signature;
        }
      } finally {
        if (!cancelled) {
          setIsSemanticTreeAnalyzing(false);
        }
      }
    }, 700);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [applySemanticTreePayload, isMatchChatReady, isMatchingMode, matchMessageIdsSignature, matchingState?.room_id]);

  useEffect(() => {
    if (isMatchingMode || isSending || !sessionId || !aiUserMessageIdsSignature) {
      return undefined;
    }

    const signature = `ai:${sessionId}:${aiUserMessageIdsSignature}`;
    if (semanticTreeAnalyzeSignatureRef.current === signature) {
      return undefined;
    }

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setIsSemanticTreeAnalyzing(true);

      try {
        const response = await api.post(`/api/dialogue/sessions/${sessionId}/semantic-tree/analyze/`);
        if (!cancelled) {
          applySemanticTreePayload(response.data);
          semanticTreeAnalyzeSignatureRef.current = signature;
        }
      } catch (error) {
        if (!cancelled) {
          setSemanticTreeStatus(error?.response?.data?.analysisStatus || 'analyze_failed');
          setSemanticTreeMessage(
            error?.response?.data?.message ||
              error?.response?.data?.detail ||
              '語意脈絡分析暫時失敗，對話仍可使用。',
          );
          semanticTreeAnalyzeSignatureRef.current = signature;
        }
      } finally {
        if (!cancelled) {
          setIsSemanticTreeAnalyzing(false);
        }
      }
    }, 700);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [aiUserMessageIdsSignature, applySemanticTreePayload, isMatchingMode, isSending, sessionId]);

  const sendCancelMatchingQueueRequest = useCallback((topicId, { keepalive = false } = {}) => {
    if (!topicId) {
      return;
    }

    const token = localStorage.getItem('access');
    if (!token) {
      return;
    }

    const url = `${window.location.origin}${import.meta.env.VITE_API_BASE_URL || ''}/api/matching/cancel/`;

    fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ topic_id: topicId }),
      keepalive,
    }).catch(() => {
      // Best-effort cleanup when user leaves while still waiting for a match.
    });
  }, []);

  const triggerAutoCancelMatchingQueue = useCallback(({ keepalive = false } = {}) => {
    if (!isMatchingMode) {
      return;
    }

    const { status, topicId } = activeMatchRef.current;
    if (status !== 'matching' || !topicId || cancelQueueRequestSentRef.current) {
      return;
    }

    cancelQueueRequestSentRef.current = true;
    sendCancelMatchingQueueRequest(topicId, { keepalive });
  }, [isMatchingMode, sendCancelMatchingQueueRequest]);

  useEffect(() => {
    if (!isMatchingMode) {
      return undefined;
    }

    const handlePageHide = () => {
      triggerAutoCancelMatchingQueue({ keepalive: true });
    };

    window.addEventListener('pagehide', handlePageHide);

    return () => {
      window.removeEventListener('pagehide', handlePageHide);
      triggerAutoCancelMatchingQueue({ keepalive: true });
    };
  }, [isMatchingMode, triggerAutoCancelMatchingQueue]);

  const connectDialogueWebSocket = (activeSessionId) => {
    const token = localStorage.getItem('access');
    if (!token) {
      throw new Error('登入已過期，請重新登入。');
    }

    const socket = new WebSocket(buildDialogueWebSocketUrl(activeSessionId, token));
    wsSessionIdRef.current = activeSessionId;

    socket.onmessage = (event) => {
      let data = null;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      if (data.type === 'agent_stream') {
        setIsAgentStreaming(true);
        setMessages((prev) => {
          const messageId = currentAgentMsgIdRef.current;
          if (messageId && prev.some((message) => message.id === messageId)) {
            return prev.map((message) =>
              message.id === messageId
                ? { ...message, text: message.text + data.content }
                : message,
            );
          }

          const newMessageId = `agent-${Date.now()}`;
          currentAgentMsgIdRef.current = newMessageId;
          return [
            ...prev,
            {
              id: newMessageId,
              type: 'agent',
              userName: 'BridgeUs',
              text: data.content,
            },
          ];
        });
        return;
      }

      if (data.type === 'agent_stream_end') {
        currentAgentMsgIdRef.current = null;
        setAiStanceDrift(extractStanceDrift(data));
        setIsAgentStreaming(false);
        setIsSending(false);
        return;
      }

      if (data.type === 'error') {
        currentAgentMsgIdRef.current = null;
        setIsAgentStreaming(false);
        setChatError(data.content || 'AI 回應中斷，請重試。');
        setIsSending(false);
      }
    };

    socket.onerror = () => {
      setIsAgentStreaming(false);
      setChatError('WebSocket 連線錯誤，請重新整理頁面。');
      setIsSending(false);
    };

    socket.onclose = () => {
      if (wsRef.current === socket) {
        wsRef.current = null;
        wsSessionIdRef.current = null;
      }

      if (isSendingRef.current) {
        setIsAgentStreaming(false);
        setChatError('連線中斷，請重新整理頁面。');
        setIsSending(false);
      }
    };

    wsRef.current = socket;
    return socket;
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
      survey_open_answers: surveyOpenAnswers,
      user_initial_argument: surveyOpenAnswers.Q9 || '',
    });

    setSessionId(response.data.session_id);
    setAiStanceMeta(extractAiStanceMeta(response.data));
    setAiStanceDrift(extractStanceDrift(response.data));
    return response.data.session_id;
  };

  const requestRestDialogueReply = async (activeSessionId, text) => {
    const response = await api.post(
      `/api/dialogue/sessions/${activeSessionId}/reply/`,
      { message: text },
    );

    setMessages(mapHistoryToMessages(response.data.history, displayUserName));
    setAiStanceMeta(extractAiStanceMeta(response.data));
    setAiStanceDrift(extractStanceDrift(response.data));
  };


  const sendMatchSuggestionAction = useCallback((type, payload = {}) => {
    const socket = matchWsRef.current;
    if (socket?.readyState !== WebSocket.OPEN) {
      setMatchChatError('配對聊天室連線尚未恢復，請稍後再試。');
      return false;
    }

    socket.send(JSON.stringify({ type, ...payload }));
    return true;
  }, []);

  const handleAcceptMatchSuggestion = useCallback(() => {
    if (!pendingMatchSuggestion?.suggestion_id) {
      return;
    }

    const sent = sendMatchSuggestionAction('accept_suggestion', {
      suggestion_id: pendingMatchSuggestion.suggestion_id,
    });
    if (sent) {
      setPendingMatchSuggestion(null);
      setMatchSuggestionDraft(null);
    }
  }, [pendingMatchSuggestion, sendMatchSuggestionAction]);

  const handleIgnoreMatchSuggestion = useCallback(() => {
    if (!pendingMatchSuggestion?.suggestion_id) {
      return;
    }

    const sent = sendMatchSuggestionAction('ignore_suggestion', {
      suggestion_id: pendingMatchSuggestion.suggestion_id,
    });
    if (sent) {
      setPendingMatchSuggestion(null);
      setMatchSuggestionDraft(null);
    }
  }, [pendingMatchSuggestion, sendMatchSuggestionAction]);

  const handleEditMatchSuggestion = useCallback(() => {
    if (!pendingMatchSuggestion?.suggestion_id) {
      return;
    }

    const suggestedText = pendingMatchSuggestion.suggested_content || '';
    setInputValue(suggestedText);
    setMatchSuggestionDraft(pendingMatchSuggestion);
    setPendingMatchSuggestion(null);
    focusChatInput();
    window.requestAnimationFrame(() => {
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
        textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
      }
    });
  }, [focusChatInput, pendingMatchSuggestion]);

  const handleCancelSuggestionDraft = useCallback(() => {
    setMatchSuggestionDraft(null);
    setInputValue('');
    focusChatInput();
  }, [focusChatInput]);

  const handleSurveySubmit = async ({ answers, openAnswers }) => {
    setSurveyAnswers(answers);
    setSurveyOpenAnswers(openAnswers);

    if (!isMatchingMode) {
      wsRef.current?.close();
      wsRef.current = null;
      wsSessionIdRef.current = null;
      currentAgentMsgIdRef.current = null;
      setSessionId(null);
      setMessages([]);
      setAiStanceMeta(null);
      setAiStanceDrift(null);
      setSemanticTreePayload(null);
      setSemanticTreeStatus('ready');
      setSemanticTreeMessage('');
      semanticTreeAnalyzeSignatureRef.current = '';
      setShowSurvey(false);
      setChatError('');
      return;
    }

    setMatchingError('');
    setIsMatchingActionLoading(true);

    try {
      const topicId = Number(id);
      const response = await api.post('/api/matching/join/', {
        topic_id: topicId,
        survey_answers: answers,
        survey_open_answers: openAnswers,
        restart_existing_match: matchingState?.status === 'matched',
      });

      if (!isChatPageMountedRef.current) {
        if (response.data?.status === 'matching') {
          sendCancelMatchingQueueRequest(topicId, { keepalive: true });
        }
        return;
      }

      setMatchingState(response.data);
      setShowSurvey(false);
    } catch (error) {
      if (!isChatPageMountedRef.current) {
        return;
      }

      setMatchingError(
        error?.response?.data?.detail ||
          '目前無法加入匹配，請稍後再試。',
      );
    } finally {
      if (isChatPageMountedRef.current) {
        setIsMatchingActionLoading(false);
      }
    }
  };

  const handleSendMessage = async () => {
    const text = inputValue.trim();
    if (!text) return;

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    if (isMatchingMode) {
      if (!isMatchChatReady || isMatchSending) {
        return;
      }

      const suggestionDraft = matchSuggestionDraft;
      setInputValue('');
      setMatchChatError('');
      setIsMatchSending(true);
      shouldAutoScrollMatchRef.current = true;
      setShowScrollToBottomButton(false);

      try {
        const socket = matchWsRef.current;
        if (suggestionDraft?.suggestion_id) {
          if (socket?.readyState !== WebSocket.OPEN) {
            setInputValue(text);
            setMatchChatError('配對聊天室連線尚未恢復，請稍後再試。');
            return;
          }

          socket.send(JSON.stringify({
            type: 'modify_suggestion',
            suggestion_id: suggestionDraft.suggestion_id,
            content: text,
          }));
          setMatchSuggestionDraft(null);
          return;
        }

        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'match_message', content: text }));
          return;
        }

        const response = await api.post(
          `/api/matching/rooms/${matchingState.room_id}/messages/`,
          { content: text },
        );
        setMatchMessages(Array.isArray(response.data?.messages) ? response.data.messages : []);
        setMatchStanceDrift(response.data?.stance_drift || null);
        setMatchingState((prev) => {
          if (!prev) {
            return prev;
          }

          return {
            ...prev,
            other_user_id: response.data?.other_user_id ?? prev.other_user_id,
            other_user_name: response.data?.other_user_name ?? prev.other_user_name,
          };
        });
      } catch (error) {
        setMatchChatError(
          error?.response?.data?.detail ||
            '目前無法送出配對聊天室訊息，請稍後再試。',
        );
      } finally {
        setIsMatchSending(false);
        focusChatInput();
      }

      return;
    }

    if (isSending) {
      return;
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
    setIsAgentStreaming(false);
    setIsSending(true);

    let activeSessionId = null;

    try {
      activeSessionId = await ensureSession();
      const existingSocket = wsRef.current;
      const needsNewSocket =
        !existingSocket ||
        existingSocket.readyState === WebSocket.CLOSED ||
        existingSocket.readyState === WebSocket.CLOSING ||
        wsSessionIdRef.current !== activeSessionId;

      const socket = needsNewSocket
        ? connectDialogueWebSocket(activeSessionId)
        : existingSocket;

      await waitForSocketOpen(socket);
      currentAgentMsgIdRef.current = null;
      socket.send(JSON.stringify({ type: 'user_message', content: text }));
    } catch (socketError) {
      wsRef.current?.close();
      wsRef.current = null;
      wsSessionIdRef.current = null;
      currentAgentMsgIdRef.current = null;
      setIsAgentStreaming(false);

      try {
        await requestRestDialogueReply(activeSessionId, text);
      } catch (restError) {
        const detail =
          restError?.response?.data?.detail ||
          socketError?.message ||
          '無法連接對話服務，請稍後再試。';

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
    }
  };

  const handleTextareaChange = (event) => {
    setInputValue(event.target.value);
    const el = event.target;
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
    void handleSendMessage();
  };

  const handleCancelMatching = async () => {
    setMatchingError('');
    setIsMatchingActionLoading(true);

    try {
      const response = await api.post('/api/matching/cancel/', {
        topic_id: Number(id),
      });

      cancelQueueRequestSentRef.current = true;
      setMatchingState(response.data);
      setShowSurvey(false);
      setMatchMessages([]);
      setMatchStanceDrift(null);
      setMatchChatError('');
      setMatchAssistNotice(null);
      setPendingMatchSuggestion(null);
      setMatchSuggestionDraft(null);
    } catch (error) {
      setMatchingError(
        error?.response?.data?.detail ||
          '目前無法取消匹配，請稍後再試。',
      );
    } finally {
      setIsMatchingActionLoading(false);
    }
  };

  const handleLeaveMatchRoom = async () => {
    if (!matchingState?.room_id) {
      return;
    }

    setMatchChatError('');
    setIsMatchingActionLoading(true);
    leaveRequestSentRef.current = true;

    try {
      const response = await api.post(
        `/api/matching/rooms/${matchingState.room_id}/leave/`,
      );
      setMatchingState(response.data);
      setMatchMessages([]);
      setMatchStanceDrift(null);
      setMatchAssistNotice(null);
      setPendingMatchSuggestion(null);
      setMatchSuggestionDraft(null);
    } catch (error) {
      leaveRequestSentRef.current = false;
      setMatchChatError(
        error?.response?.data?.detail ||
          '目前無法退出聊天室，請稍後再試。',
      );
    } finally {
      setIsMatchingActionLoading(false);
    }
  };

  const handleRestartSurvey = () => {
    setMatchingError('');
    setMatchChatError('');
    setMatchMessages([]);
    setMatchStanceDrift(null);
    setMatchAssistNotice(null);
    setPendingMatchSuggestion(null);
    setMatchSuggestionDraft(null);
    setShowSurvey(true);
  };

  const renderMatchingCard = () => {
    const matchingStatus = matchingState?.status || 'idle';
    const stanceScore = matchingState?.stance_score || '尚未建立';
    const stanceCategory = formatStanceCategory(matchingState?.stance_category);

    if (isMatchingStateLoading) {
      return (
        <div className="matching-status-shell">
          <div className="matching-status-card">
            <span className="matching-status-badge">同步中</span>
            <h2 className="matching-status-title">正在同步配對狀態</h2>
            <p className="matching-status-copy">
              系統正在確認你目前是否有等待中的匹配或已成立的對話連線。
            </p>
          </div>
        </div>
      );
    }

    if (showSurvey) {
      return (
        <div className="matching-status-shell">
          <div className="matching-status-card">
            <span className="matching-status-badge">前置步驟</span>
            <h2 className="matching-status-title">先完成立場問卷</h2>
            <p className="matching-status-copy">
              問卷送出後，系統會依目前的立場分數幫你加入配對佇列，並持續等待直到成功匹配或你手動取消。
            </p>
          </div>
        </div>
      );
    }

    if (matchingStatus === 'ai_recommended') {
      return (
        <div className="matching-status-shell">
          <div className="matching-status-card">
            <span className="matching-status-badge">建議 AI 對話</span>
            <h2 className="matching-status-title">你的立場目前較適合先和 AI 代理人討論</h2>
            <p className="matching-status-copy">
              真人配對目前優先安排立場差異明確的支持與反對使用者。你可以先進入 AI 模式整理觀點，或重新填寫問卷。
            </p>
            <div className="matching-status-meta">
              <div className="matching-status-row">
                <span className="matching-status-label">立場類型</span>
                <span className="matching-status-value">{stanceCategory}</span>
              </div>
              <div className="matching-status-row">
                <span className="matching-status-label">立場分數</span>
                <span className="matching-status-value">{stanceScore}</span>
              </div>
            </div>
            <div className="matching-status-actions">
              <button
                className="matching-status-btn secondary"
                type="button"
                onClick={() => navigate(`/topic/${id}?mode=ai`)}
              >
                前往 AI 模式
              </button>
              <button
                className="matching-status-btn secondary"
                type="button"
                onClick={handleRestartSurvey}
              >
                重新填寫問卷
              </button>
            </div>
          </div>
        </div>
      );
    }

    if (matchingStatus === 'matching') {
      return (
        <div className="matching-status-shell">
          <div className="matching-status-card is-waiting">
            <span className="matching-status-badge">配對中</span>
            <h2 className="matching-status-title">正在為你尋找可配對的對話對象</h2>
            <p className="matching-status-copy">
              你現在已經在等待佇列中。離開頁面會自動取消等待；配對成功後短暫重整頁面可以回到同一個聊天室。
            </p>
            <div className="matching-status-meta">
              <div className="matching-status-row">
                <span className="matching-status-label">立場類型</span>
                <span className="matching-status-value">{stanceCategory}</span>
              </div>
              <div className="matching-status-row">
                <span className="matching-status-label">立場分數</span>
                <span className="matching-status-value">{stanceScore}</span>
              </div>
              <div className="matching-status-row">
                <span className="matching-status-label">開始等待</span>
                <span className="matching-status-value">
                  {formatTimestamp(matchingState?.waiting_started_at)}
                </span>
              </div>
            </div>
            <div className="matching-status-actions">
              <button
                className="matching-status-btn danger"
                type="button"
                onClick={handleCancelMatching}
                disabled={isMatchingActionLoading}
              >
                {isMatchingActionLoading ? '取消中...' : '取消匹配'}
              </button>
            </div>
          </div>
        </div>
      );
    }

    if (matchingStatus === 'matched') {
      const matchChatDisplayMessages = mapMatchMessagesToDisplay(
        matchMessages,
        user?.id,
      );

      return (
        <>
          <div className="match-room-banner">
            <span className="matching-status-badge">配對成功</span>
            <h2 className="match-room-banner-title">已開始匿名對話</h2>
            <div className="match-room-banner-meta">
              <span>房間：{matchingState?.room_id || '—'}</span>
              <span>匹配完成：{formatTimestamp(matchingState?.matched_at)}</span>
            </div>
            {matchPresenceNotice && (
              <p className="match-room-banner-presence">{matchPresenceNotice}</p>
            )}
            <div className="matching-status-actions">
              <button
                className="matching-status-btn secondary"
                type="button"
                onClick={handleRestartSurvey}
                disabled={isMatchingActionLoading}
              >
                重新填寫問卷
              </button>
              <button
                className="matching-status-btn secondary"
                type="button"
                onClick={handleLeaveMatchRoom}
                disabled={isMatchingActionLoading}
              >
                {isMatchingActionLoading ? '退出中...' : '退出聊天室'}
              </button>
            </div>
          </div>
          {isMatchMessagesLoading && matchMessages.length === 0 && (
            <div className="match-message-empty">正在載入對話紀錄...</div>
          )}
          {!isMatchMessagesLoading && matchMessages.length === 0 && (
            <div className="match-message-empty">配對成功了，現在可以先說第一句。</div>
          )}
          {matchAssistNotice && (
            <div className="match-assist-card system">
              <div>
                <span className="match-assist-label">系統提醒</span>
                <p className="match-assist-copy">{matchAssistNotice.message}</p>
              </div>
              <button
                className="match-assist-btn secondary"
                type="button"
                onClick={() => setMatchAssistNotice(null)}
              >
                知道了
              </button>
            </div>
          )}
          {pendingMatchSuggestion && (
            <div className="match-assist-card">
              <div className="match-assist-content">
                <span className="match-assist-label">
                  {formatSuggestionCategory(pendingMatchSuggestion.category)}
                </span>
                {pendingMatchSuggestion.original_content && (
                  <p className="match-assist-original">
                    原文：{pendingMatchSuggestion.original_content}
                  </p>
                )}
                <p className="match-assist-copy">
                  {pendingMatchSuggestion.suggested_content}
                </p>
              </div>
              <div className="match-assist-actions">
                {pendingMatchSuggestion.actions?.includes('accept') && (
                  <button
                    className="match-assist-btn primary"
                    type="button"
                    onClick={handleAcceptMatchSuggestion}
                  >
                    {pendingMatchSuggestion.category === 'rephrase' ? '使用建議' : '知道了'}
                  </button>
                )}
                {pendingMatchSuggestion.actions?.includes('modify') && (
                  <button
                    className="match-assist-btn secondary"
                    type="button"
                    onClick={handleEditMatchSuggestion}
                  >
                    放到輸入框修改
                  </button>
                )}
                {pendingMatchSuggestion.actions?.includes('ignore') && (
                  <button
                    className="match-assist-btn ghost"
                    type="button"
                    onClick={handleIgnoreMatchSuggestion}
                  >
                    {pendingMatchSuggestion.category === 'rephrase' ? '仍送出原文' : '略過'}
                  </button>
                )}
              </div>
            </div>
          )}
          {matchSuggestionDraft && (
            <div className="match-assist-card editing">
              <div>
                <span className="match-assist-label">正在修改 AI 建議</span>
                <p className="match-assist-copy">
                  編輯完成後按送出，系統會再次檢查語氣後送出。
                </p>
              </div>
              <button
                className="match-assist-btn ghost"
                type="button"
                onClick={handleCancelSuggestionDraft}
              >
                取消修改
              </button>
            </div>
          )}
          {matchChatDisplayMessages.map((msg) => (
            <div key={msg.id} className={`message-row ${msg.type === 'user' ? 'user-message' : ''}`}>
              <div className="message-user-info">
                <img src="/icon.jpg" alt="Avatar" className="message-avatar" />
                <span className="message-username">{msg.userName}</span>
              </div>
              <div className="message-bubble">{msg.text}</div>
            </div>
          ))}
          {isMatchSending && (
            <div className="message-row user-message">
              <div className="message-user-info">
                <img src="/icon.jpg" alt="Avatar" className="message-avatar" />
                <span className="message-username">{MATCH_SELF_NAME}</span>
              </div>
              <div className="message-bubble">正在送出訊息...</div>
            </div>
          )}
        </>
      );
    }

    if (matchingStatus === 'closed') {
      return (
        <div className="matching-status-shell">
          <div className="matching-status-card is-cancelled">
            <span className="matching-status-badge">聊天室已結束</span>
            <h2 className="matching-status-title">你已退出這次配對聊天室</h2>
            <p className="matching-status-copy">
              這次真人配對對話已結束。若要重新開始，請重新填寫問卷並再次加入匹配佇列。
            </p>
            <div className="matching-status-meta">
              <div className="matching-status-row">
                <span className="matching-status-label">結束時間</span>
                <span className="matching-status-value">
                  {formatTimestamp(matchingState?.closed_at)}
                </span>
              </div>
            </div>
            <div className="matching-status-actions">
              <button
                className="matching-status-btn secondary"
                type="button"
                onClick={handleRestartSurvey}
              >
                重新填寫問卷
              </button>
            </div>
          </div>
        </div>
      );
    }

    if (matchingStatus === 'cancelled') {
      return (
        <div className="matching-status-shell">
          <div className="matching-status-card is-cancelled">
            <span className="matching-status-badge">已取消</span>
            <h2 className="matching-status-title">這次匹配已停止</h2>
            <p className="matching-status-copy">
              你已手動取消等待中的配對。若要重新開始，請重新填寫問卷並再次加入匹配佇列。
            </p>
            <div className="matching-status-meta">
              <div className="matching-status-row">
                <span className="matching-status-label">取消時間</span>
                <span className="matching-status-value">
                  {formatTimestamp(matchingState?.cancelled_at)}
                </span>
              </div>
            </div>
            <div className="matching-status-actions">
              <button
                className="matching-status-btn secondary"
                type="button"
                onClick={handleRestartSurvey}
              >
                重新填寫問卷
              </button>
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="matching-status-shell">
        <div className="matching-status-card">
          <span className="matching-status-badge">待開始</span>
          <h2 className="matching-status-title">尚未加入匹配佇列</h2>
          <p className="matching-status-copy">
            完成立場問卷後，系統才會開始為你尋找可配對的對話對象。
          </p>
        </div>
      </div>
    );
  };

  if (!issuesLoaded) {
    return <div style={{ padding: '50px', textAlign: 'center' }}>正在載入議題數據...</div>;
  }

  if (!currentIssue) {
    return <div style={{ padding: '50px', textAlign: 'center' }}>找不到這個議題，請返回首頁重新選擇。</div>;
  }

  const inputPlaceholder = isMatchingMode
    ? showSurvey || isSurveyLoading
      ? '請先完成立場檢測問卷'
      : isMatchingStateLoading || isMatchingActionLoading
        ? '正在同步配對狀態...'
        : isMatchChatReady
          ? `輸入訊息給 ${matchPartnerName}...`
          : matchingState?.status === 'matching'
            ? '正在為你尋找可配對的對話對象'
            : matchingState?.status === 'closed'
              ? '聊天室已結束，可重新填寫問卷'
              : matchingState?.status === 'cancelled'
                ? '已取消匹配，可重新填寫問卷'
                : '請先加入匹配佇列'
    : isSessionRestoring
      ? '正在恢復最近一次 AI 對話...'
      : pendingRestoredSession
        ? '請先選擇繼續上次對話或開始新對話'
      : showSurvey
      ? '請先完成立場檢測問卷'
      : '輸入觀點...';

  const isInputDisabled = isMatchingMode
    ? !isMatchChatReady || isMatchSending
    : showSurvey || isSending || isSessionRestoring || Boolean(pendingRestoredSession);

  const activeChatError = isMatchingMode && showSurvey
    ? ''
    : isMatchingMode
      ? matchChatError
      : chatError;
  const otherPresence = matchingState?.presence?.other_user;
  const matchPresenceNotice = isMatchChatReady && otherPresence?.connected === false
    ? `匿名對話者暫時離線，若持續到 ${formatTimestamp(
        matchingState?.absence_deadline || matchingState?.presence?.absence_deadline,
      )} 對話會自動結束。`
    : '';
  const driftUpdatedAt = matchStanceDrift?.measured_at
    ? `更新：${formatTimestamp(matchStanceDrift.measured_at)}`
    : '傳送訊息後由系統計算';
  const aiDriftUpdatedAt = aiStanceDrift?.measured_at
    ? `更新：${formatTimestamp(aiStanceDrift.measured_at)}`
    : '傳送訊息後由系統計算';
  const stanceScoreDisplay = isMatchingMode
    ? formatStanceScoreValue(matchingState?.stance_score)
    : formatStanceScoreValue(aiStanceMeta?.stanceScore);
  const stanceCategoryDisplay = isMatchingMode
    ? formatStanceCategory(matchingState?.stance_category)
    : aiStanceMeta?.stanceLabel || formatStanceCategory(aiStanceMeta?.stanceCategory);
  const matchMessageCount = isMatchingMode && isMatchChatReady
    ? matchMessages.length
    : 0;
  const aiUserMessageCount = !isMatchingMode
    ? messages.filter((message) => message.type === 'user').length
    : 0;
  const metricMessageCount = isMatchingMode ? matchMessageCount : messages.length;
  const semanticTreeMessageCount = isMatchingMode ? matchMessageCount : aiUserMessageCount;
  const isSemanticTreeActive = isMatchingMode ? isMatchChatReady : Boolean(sessionId);
  const driftValueDisplay = isMatchingMode
    ? formatDriftValue(matchStanceDrift?.drift_value)
    : formatDriftValue(aiStanceDrift?.drift_value);
  const driftHintDisplay = isMatchingMode
    ? driftUpdatedAt
    : aiDriftUpdatedAt;
  const metricMessageHint = isMatchingMode ? '目前房間累計訊息' : '目前 AI 對話累計訊息';

  return (
    <div className="chat-page-container">
      {showSurvey && (
        <SurveyModal
          isOpen={showSurvey}
          survey={survey}
          isLoading={isSurveyLoading}
          error={surveyError}
          isSubmitting={isMatchingMode ? isMatchingActionLoading : false}
          submitError={isMatchingMode ? matchingError : ''}
          onSubmit={handleSurveySubmit}
        />
      )}

      {!isRightPanelOpen && (
        <button
          className="panel-toggle-btn"
          onClick={() => setIsRightPanelOpen(true)}
          aria-label="展開功能面板"
        >
          ⊞
        </button>
      )}

      {isRightPanelOpen && (
        <div className="panel-backdrop" onClick={() => setIsRightPanelOpen(false)} />
      )}

      <div className={`chat-left-interaction-area ${!showSurvey ? 'content-visible' : ''}`}>
        <div className="current-topic-section">
          <div className="topic-title-row">
            <h1 className="topic-title">{currentIssue?.title || '未知的領域'}</h1>
            <span className={`topic-mode-badge ${isMatchingMode ? 'mode-match' : 'mode-ai'}`}>
              {modeLabel}
            </span>
          </div>
        </div>

        <div
          ref={messagesContainerRef}
          className="chat-messages-display"
          onScroll={handleMessagesScroll}
        >
          {isMatchingMode ? (
            <>
              {renderMatchingCard()}
              {matchingError && !showSurvey && matchingState?.status !== 'matched' && (
                <p className="matching-status-error">{matchingError}</p>
              )}
            </>
          ) : (
            <>
              {pendingRestoredSession && (
                <div className="ai-session-choice-card">
                  <span className="ai-session-choice-kicker">找到上次 AI 對話</span>
                  <h2 className="ai-session-choice-title">要繼續上次，還是開始新對話？</h2>
                  <p className="ai-session-choice-copy">
                    上次對話有 {pendingRestoredUserMessageCount} 則你的發言。
                    {pendingRestoredPreview
                      ? ` 最近內容：「${pendingRestoredPreview.slice(0, 80)}${pendingRestoredPreview.length > 80 ? '...' : ''}」`
                      : ' 你也可以直接重新填寫問卷建立新的對話脈絡。'}
                  </p>
                  <div className="ai-session-choice-actions">
                    <button
                      className="ai-session-choice-btn primary"
                      type="button"
                      onClick={handleContinueRestoredSession}
                    >
                      繼續上次對話
                    </button>
                    <button
                      className="ai-session-choice-btn secondary"
                      type="button"
                      onClick={handleStartNewAiDialogue}
                    >
                      開始新對話
                    </button>
                  </div>
                </div>
              )}
              {messages.map((msg) => (
                <div key={msg.id} className={`message-row ${msg.type === 'user' ? 'user-message' : ''}`}>
                  <div className="message-user-info">
                    <img src="/icon.jpg" alt="Avatar" className="message-avatar" />
                    <span className="message-username">{msg.userName}</span>
                  </div>
                  <div className="message-bubble">{msg.text}</div>
                </div>
              ))}
              {isSending && !isAgentStreaming && (
                <div className="message-row">
                  <div className="message-user-info">
                    <img src="/icon.jpg" alt="Avatar" className="message-avatar" />
                    <span className="message-username">BridgeUs</span>
                  </div>
                  <div className="message-bubble">正在整理回應...</div>
                </div>
              )}
            </>
          )}
          <div ref={messagesEndRef} />
        </div>

        {isMatchingMode && isMatchChatReady && showScrollToBottomButton && (
          <button
            className="scroll-to-bottom-btn"
            type="button"
            onClick={handleScrollToBottom}
            onMouseDown={(event) => {
              event.preventDefault();
            }}
          >
            回到底部
          </button>
        )}

        <div className="chat-input-area">
          <div className={`chat-input-wrapper ${isMatchingMode && !isMatchChatReady ? 'is-disabled' : ''}`}>
            <textarea
              ref={textareaRef}
              className="chat-text-input"
              placeholder={inputPlaceholder}
              value={inputValue}
              disabled={isInputDisabled}
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
              onMouseDown={(event) => {
                event.preventDefault();
              }}
              onClick={() => {
                void handleSendMessage();
              }}
              disabled={isInputDisabled || !inputValue.trim()}
              aria-label="發送訊息"
            >
              <img src="/arrow-right.png" alt="發送" className="send-icon" />
            </button>
          </div>
          {activeChatError && (
            <p style={{ color: '#b42318', fontSize: '14px', marginTop: '8px' }}>
              {activeChatError}
            </p>
          )}
        </div>
      </div>

      <aside className={`chat-right-function-area${isRightPanelOpen ? ' panel-open' : ''}`}>
        <div className="right-major-feature-box">
          <ConversationTreePanel
            topicTitle={currentIssue?.title || '未知的領域'}
            treeData={semanticTreePayload?.treeData || null}
            trees={semanticTreePayload?.trees || []}
            messageCount={semanticTreeMessageCount}
            mode={isMatchingMode ? 'matching' : 'ai'}
            isActive={isSemanticTreeActive}
            isLoading={isSemanticTreeLoading}
            isAnalyzing={isSemanticTreeAnalyzing}
            analysisStatus={semanticTreeStatus}
            analysisMessage={semanticTreeMessage}
          />
        </div>

        <div className="right-minor-feature-row" aria-label="對話即時指標">
          <div className="minor-feature-box metric-card">
            <span className="metric-label">我的立場偏離度</span>
            <strong className="metric-value">
              {driftValueDisplay}
            </strong>
            <span className="metric-hint">
              {driftHintDisplay}
            </span>
          </div>
          <div className="minor-feature-box metric-card">
            <span className="metric-label">我的立場分數</span>
            <strong className="metric-value">{stanceScoreDisplay}</strong>
            <span className="metric-hint">{stanceCategoryDisplay}</span>
          </div>
          <div className="minor-feature-box metric-card">
            <span className="metric-label">對話訊息數</span>
            <strong className="metric-value">{metricMessageCount}</strong>
            <span className="metric-hint">{metricMessageHint}</span>
          </div>
        </div>
      </aside>
    </div>
  );
}

export default TopicChat;
