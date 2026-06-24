import { useEffect, useState } from 'react';
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

function HistoryPage() {
  const [filter, setFilter] = useState('all');
  const [items, setItems] = useState([]);
  const [selectedItem, setSelectedItem] = useState(null);
  const [detail, setDetail] = useState(null);
  const [isListLoading, setIsListLoading] = useState(false);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const [analysisError, setAnalysisError] = useState('');

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
          treeData={treePayload?.treeData || null}
          trees={treePayload?.trees || []}
          messageCount={detail?.messages?.length || 0}
          mode={detail?.kind === 'match' ? 'matching' : 'ai'}
          isActive={Boolean(detail)}
          isLoading={isDetailLoading}
          isAnalyzing={isAnalyzing}
          analysisStatus={treePayload?.analysisStatus || 'ready'}
        />
      </section>
    </div>
  );
}

export default HistoryPage;
