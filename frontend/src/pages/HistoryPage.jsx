import { useEffect, useRef, useState } from 'react';
import api from '../api/client';
import ConversationTreePanel from '../components/ConversationTreePanel';
import './HistoryPage.css';

const FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'ai', label: 'AI 對話' },
  { id: 'match', label: '真人對話' },
];

function formatHistoryTime(value) {
  if (!value) {
    return '時間未知';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return new Intl.DateTimeFormat('zh-TW', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function conversationTypeLabel(kind) {
  return kind === 'match' ? '真人對話' : 'AI 對話';
}

function messageRowClass(role) {
  return role === 'user' ? 'history-message-row is-user' : 'history-message-row';
}

function hasAnalyzedTree(treePayload) {
  return Array.isArray(treePayload?.analyzedSourceIds) && treePayload.analyzedSourceIds.length > 0;
}

// CCND 時間軸只支援真人配對房間（M3 semantic-tree/timeline/ API 目前只認 room_id），
// AI 對話的想法脈絡圖沒有對應的後端 endpoint，所以時間軸控制項只在 kind === 'match' 時顯示。
function timelineMessagesFor(detail) {
  if (!detail || detail.kind !== 'match' || !Array.isArray(detail.messages)) {
    return [];
  }
  return detail.messages;
}

function HistoryPage() {
  const [filter, setFilter] = useState('all');
  const [items, setItems] = useState([]);
  const [selectedItem, setSelectedItem] = useState(null);
  const [detail, setDetail] = useState(null);
  const [isListLoading, setIsListLoading] = useState(false);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const [timelineIndex, setTimelineIndex] = useState(null); // null = 顯示目前最新狀態
  const [timelineTreeData, setTimelineTreeData] = useState(null);
  const [isTimelineLoading, setIsTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState('');
  const [analysisError, setAnalysisError] = useState('');
  // 每次切換對話或重新拖動時間軸都會+1，讓晚到的舊請求發現自己已經過期，
  // 不會在使用者已經切到別筆對話之後才把過期的樹狀態蓋上去。
  const timelineRequestIdRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    const loadHistory = async () => {
      setIsListLoading(true);
      setError('');
      setAnalysisError('');
      try {
        const response = await api.get(`/api/history/conversations/?type=${filter}`);
        if (cancelled) return;

        const nextItems = Array.isArray(response.data?.results) ? response.data.results : [];
        setItems(nextItems);
        if (!nextItems.length) {
          setDetail(null);
        }
        setSelectedItem((current) => {
          if (current && nextItems.some((item) => item.kind === current.kind && item.id === current.id)) {
            return current;
          }
          return nextItems[0] || null;
        });
      } catch (requestError) {
        if (cancelled) return;
        setItems([]);
        setSelectedItem(null);
        setDetail(null);
        setError(requestError?.response?.data?.detail || '目前無法讀取歷史對話。');
      } finally {
        if (!cancelled) {
          setIsListLoading(false);
        }
      }
    };

    void loadHistory();

    return () => {
      cancelled = true;
    };
  }, [filter]);

  useEffect(() => {
    if (!selectedItem) {
      return undefined;
    }

    let cancelled = false;

    const loadDetail = async () => {
      setIsDetailLoading(true);
      setAnalysisError('');
      setTimelineIndex(null);
      setTimelineTreeData(null);
      setTimelineError('');
      timelineRequestIdRef.current += 1;
      try {
        const response = await api.get(`/api/history/conversations/${selectedItem.kind}/${selectedItem.id}/`);
        if (!cancelled) {
          setDetail(response.data);
        }
      } catch (requestError) {
        if (!cancelled) {
          setDetail(null);
          setAnalysisError(requestError?.response?.data?.detail || '目前無法讀取這筆對話。');
        }
      } finally {
        if (!cancelled) {
          setIsDetailLoading(false);
        }
      }
    };

    void loadDetail();

    return () => {
      cancelled = true;
    };
  }, [selectedItem]);

  const handleAnalyzeTree = async () => {
    if (!detail || isAnalyzing) {
      return;
    }

    setIsAnalyzing(true);
    setAnalysisError('');
    try {
      const response = await api.post(
        `/api/history/conversations/${detail.kind}/${detail.id}/semantic-tree/analyze/`,
      );
      setDetail(response.data);
    } catch (requestError) {
      setAnalysisError(
        requestError?.response?.data?.message ||
          requestError?.response?.data?.detail ||
          '目前無法整理想法脈絡圖。',
      );
    } finally {
      setIsAnalyzing(false);
    }
  };

  const treePayload = detail?.semantic_tree;
  const shouldShowAnalyzeButton = detail && !hasAnalyzedTree(treePayload);
  const timelineMessages = timelineMessagesFor(detail);
  const isViewingLatest = timelineIndex === null || timelineIndex === timelineMessages.length - 1;

  // 拖動中只更新滑桿位置本身，不打 API，避免每拖一格就發一次請求；
  // 放開滑桿（或用鍵盤操作放開）時才真的去抓那個時間點的樹狀態。
  const handleTimelineDrag = (event) => {
    setTimelineIndex(Number(event.target.value));
  };

  const handleTimelineCommit = async (event) => {
    const index = Number(event.target.value);
    if (!detail || !timelineMessages.length) {
      return;
    }

    setTimelineIndex(index);
    setTimelineError('');
    // 每次放開滑桿都先讓前一個還沒回來的請求失效，不管這次放開的位置
    // 要不要重新打 API——否則舊請求晚一步回來時可能蓋掉這次的畫面。
    const requestId = ++timelineRequestIdRef.current;

    // 拖到最新一格效果等同於目前的即時樹，不用另外呼叫 timeline API。
    if (index === timelineMessages.length - 1) {
      setTimelineTreeData(null);
      return;
    }

    const targetMessage = timelineMessages[index];
    setIsTimelineLoading(true);
    try {
      const response = await api.get(`/api/matching/rooms/${detail.id}/semantic-tree/timeline/`, {
        // message.id 是「match-38」這種前綴過的 React key，semantic tree 記錄的
        // sourceMessageId 對應的是原始訊息 id，要用 source_id 才會查得到。
        params: { as_of_message_id: targetMessage.source_id },
      });
      // 這段等待期間使用者可能已經切換到別筆對話或拖到別的時間點，
      // 這種情況下這個回應已經過期，不能再套用到畫面上。
      if (requestId !== timelineRequestIdRef.current) {
        return;
      }
      setTimelineTreeData(response.data?.treeData || null);
    } catch (requestError) {
      if (requestId !== timelineRequestIdRef.current) {
        return;
      }
      setTimelineTreeData(null);
      setTimelineError(
        requestError?.response?.status === 404
          ? '這則訊息還在分析中，暫時看不到當時的想法脈絡圖。'
          : requestError?.response?.data?.detail || '目前無法讀取這個時間點的想法脈絡圖。',
      );
    } finally {
      if (requestId === timelineRequestIdRef.current) {
        setIsTimelineLoading(false);
      }
    }
  };

  return (
    <div className="history-page">
      <section className="history-list-panel">
        <div className="history-page-heading">
          <span className="history-page-kicker">History</span>
          <h1>歷史對話</h1>
          <p>回顧 AI 與真人配對紀錄，查看完整訊息與我的想法脈絡圖。</p>
        </div>

        <div className="history-filter-row" role="tablist" aria-label="歷史對話類型">
          {FILTERS.map((item) => (
            <button
              key={item.id}
              className={`history-filter-btn${filter === item.id ? ' is-active' : ''}`}
              type="button"
              onClick={() => setFilter(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="history-record-list">
          {isListLoading && <div className="history-empty-card">正在讀取歷史紀錄...</div>}
          {!isListLoading && error && <div className="history-empty-card error">{error}</div>}
          {!isListLoading && !error && items.length === 0 && (
            <div className="history-empty-card">目前還沒有可顯示的歷史對話。</div>
          )}
          {!isListLoading && items.map((item) => (
            <button
              key={`${item.kind}-${item.id}`}
              className={`history-record-card${selectedItem?.kind === item.kind && selectedItem?.id === item.id ? ' is-active' : ''}`}
              type="button"
              onClick={() => setSelectedItem(item)}
            >
              <span className={`history-kind-badge kind-${item.kind}`}>
                {conversationTypeLabel(item.kind)}
              </span>
              <strong>{item.topic_title || `議題 ${item.topic_id}`}</strong>
              <span className="history-preview">
                {item.last_message_preview || '尚無內容摘要'}
              </span>
              <span className="history-card-meta">
                {item.message_count} 則訊息 · {formatHistoryTime(item.last_activity_at)}
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="history-message-panel">
        <div className="history-panel-header">
          <span className="history-page-kicker">
            {detail ? conversationTypeLabel(detail.kind) : 'Conversation'}
          </span>
          <h2>{detail?.topic_title || '選擇一筆對話'}</h2>
          {detail && (
            <p>{detail.message_count} 則訊息 · {formatHistoryTime(detail.last_activity_at)}</p>
          )}
        </div>

        <div className="history-message-list">
          {isDetailLoading && <div className="history-empty-card">正在讀取對話內容...</div>}
          {!isDetailLoading && analysisError && !detail && (
            <div className="history-empty-card error">{analysisError}</div>
          )}
          {!isDetailLoading && !detail && !analysisError && (
            <div className="history-empty-card">請從左側選擇一筆歷史對話。</div>
          )}
          {!isDetailLoading && detail?.messages?.length === 0 && (
            <div className="history-empty-card">這筆對話沒有訊息內容。</div>
          )}
          {!isDetailLoading && detail?.messages?.map((message) => (
            <article className={messageRowClass(message.role)} key={message.id}>
              <span>{message.sender_label}</span>
              <p>{message.content}</p>
              <small>{formatHistoryTime(message.created_at)}</small>
            </article>
          ))}
        </div>
      </section>

      <section className="history-tree-panel">
        <div className="history-tree-action">
          {shouldShowAnalyzeButton && (
            <button
              className="history-analyze-btn"
              type="button"
              onClick={handleAnalyzeTree}
              disabled={isAnalyzing}
            >
              {isAnalyzing ? '整理中...' : '整理想法脈絡圖'}
            </button>
          )}
          {analysisError && detail && <span className="history-analysis-error">{analysisError}</span>}
        </div>
        <ConversationTreePanel
          topicTitle={detail?.topic_title || '想法脈絡圖'}
          treeData={(isViewingLatest ? treePayload?.treeData : timelineTreeData) || null}
          trees={isViewingLatest ? treePayload?.trees || [] : []}
          messageCount={detail?.messages?.length || 0}
          mode={detail?.kind === 'match' ? 'matching' : 'ai'}
          isActive={Boolean(detail)}
          isLoading={isDetailLoading}
          isAnalyzing={isAnalyzing}
          analysisStatus={treePayload?.analysisStatus || 'ready'}
        />
        {hasAnalyzedTree(treePayload) && timelineMessages.length > 1 && (
          <div className="history-timeline-bar">
            <div className="history-timeline-row">
              <span className="history-timeline-label">
                {isViewingLatest
                  ? '目前狀態（即時）'
                  : `第 ${timelineIndex + 1} 則訊息時的狀態 · ${formatHistoryTime(timelineMessages[timelineIndex]?.created_at)}`}
                {isTimelineLoading && '（讀取中…）'}
              </span>
            </div>
            <input
              className="history-timeline-slider"
              type="range"
              min={0}
              max={timelineMessages.length - 1}
              value={timelineIndex === null ? timelineMessages.length - 1 : timelineIndex}
              onChange={handleTimelineDrag}
              onMouseUp={handleTimelineCommit}
              onTouchEnd={handleTimelineCommit}
              onKeyUp={handleTimelineCommit}
              disabled={isTimelineLoading}
            />
            {timelineError && <span className="history-timeline-error">{timelineError}</span>}
          </div>
        )}
      </section>
    </div>
  );
}

export default HistoryPage;
