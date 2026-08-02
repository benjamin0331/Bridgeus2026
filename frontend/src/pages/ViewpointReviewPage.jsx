import { useEffect, useState, useMemo } from 'react';
import api from '../api/client';
import './ViewpointReviewPage.css';

// 研究者專用頁面。Sidebar 只在 user.isResearcher 時才顯示連結，但實際存取
// 控制一律在後端 IsResearcher（api/permissions.py）：這裡列的是還沒定案的
// 候選觀點，不是給參與者看的東西，一般帳號直接開 /viewpoint-review 也只會
// 收到 403。

const TABS = [
  { id: 'pending', label: '待審核' },
  { id: 'approved', label: '已通過' },
  { id: 'rejected', label: '未通過' },
  { id: 'all', label: '全部' },
];

const STATUS_LABELS = {
  pending: '待審核',
  approved: '已通過',
  rejected: '未通過',
};

function formatTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-TW', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function truncate(text, maxLength = 60) {
  const cleaned = String(text || '').replace(/\s+/g, ' ').trim();
  if (!cleaned) return '（空白）';
  return cleaned.length <= maxLength ? cleaned : `${cleaned.slice(0, maxLength)}…`;
}

// 同一場對話（同一個聊天室）產生的多筆觀點依 dialogue_summary_id 分組，
// 讓審核者可以一次把同一場對話的候選觀點都看過、審完，而不是被清單依分數
// 排序打散成互不相關的項目。保留原本清單的排序（Map 插入順序 = 第一次
// 出現該 summary_id 的順序）。
function groupItemsBySummary(items) {
  const groups = [];
  const bySummaryId = new Map();
  items.forEach((item) => {
    const key = item.dialogue_summary_id;
    let group = bySummaryId.get(key);
    if (!group) {
      group = { summaryId: key, items: [] };
      bySummaryId.set(key, group);
      groups.push(group);
    }
    group.items.push(item);
  });
  return groups;
}

function ViewpointReviewPage() {
  const [status, setStatus] = useState('pending');
  const [topics, setTopics] = useState([]);
  const [selectedTopicId, setSelectedTopicId] = useState(null);
  const [items, setItems] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [notes, setNotes] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionError, setActionError] = useState('');
  const [notesResetForId, setNotesResetForId] = useState(selectedId);
  // 同一場對話分組後預設折疊，只顯示分數最高的第一筆；點箭頭才展開看其他筆。
  const [expandedGroupIds, setExpandedGroupIds] = useState(() => new Set());

  useEffect(() => {
    let cancelled = false;

    api
      .get('/api/dialogue/topics/')
      .then((response) => {
        if (!cancelled) setTopics(Array.isArray(response.data) ? response.data : []);
      })
      .catch(() => {
        if (!cancelled) setTopics([]);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadList = async () => {
      setIsLoading(true);
      setError('');
      try {
        const params = { status };
        if (selectedTopicId) params.topic_id = selectedTopicId;
        const response = await api.get('/api/summary/viewpoints/', { params });
        if (cancelled) return;
        const nextItems = Array.isArray(response.data) ? response.data : [];
        setItems(nextItems);
        setSelectedId((current) =>
          nextItems.some((item) => item.id === current) ? current : nextItems[0]?.id ?? null
        );
      } catch (requestError) {
        if (cancelled) return;
        setItems([]);
        setSelectedId(null);
        setError(
          requestError?.response?.status === 403
            ? '這個頁面只開放給研究者帳號使用。'
            : requestError?.response?.data?.detail || '目前無法讀取觀點清單。'
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    void loadList();

    return () => {
      cancelled = true;
    };
  }, [status, selectedTopicId]);

  const topicTitleById = topics.reduce((acc, topic) => {
    acc[topic.id] = topic.title;
    return acc;
  }, {});

  const groupedItems = useMemo(() => groupItemsBySummary(items), [items]);

  const toggleGroupExpanded = (summaryId) => {
    setExpandedGroupIds((current) => {
      const next = new Set(current);
      if (next.has(summaryId)) next.delete(summaryId);
      else next.add(summaryId);
      return next;
    });
  };

  // 選中的觀點換了就清空備註/錯誤訊息；照 React 官方建議在渲染時直接調整
  // state（比對 selectedId 是否變過），不要在 useEffect 裡同步呼叫 setState
  // 觸發連鎖重繪。
  if (notesResetForId !== selectedId) {
    setNotesResetForId(selectedId);
    setNotes('');
    setActionError('');
  }

  const selected = items.find((item) => item.id === selectedId) || null;

  const handleDecision = async (action) => {
    if (!selected || isSubmitting) return;
    setIsSubmitting(true);
    setActionError('');
    try {
      await api.post(`/api/summary/viewpoints/${selected.id}/review/`, { action, notes });
      setItems((current) => current.filter((item) => item.id !== selected.id));
      setSelectedId(null);
    } catch (requestError) {
      setActionError(requestError?.response?.data?.detail || '操作失敗，請再試一次。');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="vr-page">
      <section className="vr-list-panel">
        <div className="vr-heading">
          <span className="vr-kicker">M6 · Step 4</span>
          <h1>觀點知識庫人工終審</h1>
          <p>審核 pipeline 篩選出的候選觀點，決定是否收錄進觀點知識庫。</p>
        </div>

        <div className="vr-tab-row" role="tablist" aria-label="審核狀態">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`vr-tab-btn${status === tab.id ? ' is-active' : ''}`}
              onClick={() => setStatus(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="vr-topic-row" role="tablist" aria-label="議題篩選">
          <button
            type="button"
            className={`vr-topic-btn${selectedTopicId === null ? ' is-active' : ''}`}
            onClick={() => setSelectedTopicId(null)}
          >
            全部議題
          </button>
          {topics.map((topic) => (
            <button
              key={topic.id}
              type="button"
              className={`vr-topic-btn${selectedTopicId === topic.id ? ' is-active' : ''}`}
              onClick={() => setSelectedTopicId(topic.id)}
            >
              {topic.title}
            </button>
          ))}
        </div>

        <div className="vr-list">
          {isLoading && <div className="vr-empty-card">正在讀取...</div>}
          {!isLoading && error && <div className="vr-empty-card error">{error}</div>}
          {!isLoading && !error && items.length === 0 && (
            <div className="vr-empty-card">這個分類目前沒有資料。</div>
          )}
          {!isLoading && groupedItems.map((group) => {
            const hasMultiple = group.items.length > 1;
            const isExpanded = expandedGroupIds.has(group.summaryId);
            // 折疊時只顯示第一筆——清單本身已經照分數排序，第一筆就是這場
            // 對話裡分數最高的那則，跟知識庫分組卡片預設只顯示最高分那筆
            // 是同一套邏輯。
            const visibleItems = hasMultiple && !isExpanded ? group.items.slice(0, 1) : group.items;
            return (
              <div
                key={group.summaryId}
                className="vr-group"
                onDoubleClick={hasMultiple ? () => toggleGroupExpanded(group.summaryId) : undefined}
              >
                {visibleItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`vr-card${selectedId === item.id ? ' is-active' : ''}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <span className="vr-card-dimension">
                      {topicTitleById[item.topic_id] ?? `topic ${item.topic_id}`} · {item.dimension}
                    </span>
                    <span className="vr-card-text">{truncate(item.user_input_text)}</span>
                    <span className="vr-card-meta">
                      {`分數 ${item.composite_score?.toFixed(4) ?? '—'} · ${formatTime(item.created_at)}`}
                    </span>
                  </button>
                ))}
                {hasMultiple && (
                  <button
                    type="button"
                    className="vr-group-toggle"
                    onClick={() => toggleGroupExpanded(group.summaryId)}
                    aria-expanded={isExpanded}
                  >
                    <span className="vr-group-label">同一場對話 · {group.items.length} 則</span>
                    <span className={`vr-group-arrow${isExpanded ? ' is-expanded' : ''}`}>▾</span>
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <section className="vr-detail-panel">
        {!selected && <div className="vr-empty-card">請從左側選擇一筆觀點。</div>}
        {selected && (
          <>
            <div className="vr-detail-header">
              <span className="vr-detail-topic">
                {`${topicTitleById[selected.topic_id] ?? `topic ${selected.topic_id}`} · ${selected.dimension}`}
              </span>
              <span className="vr-detail-dialogue">{`來源對話 ${selected.dialogue_id}`}</span>
            </div>

            <div className="vr-detail-block">
              <h3>使用者發言</h3>
              <p>{selected.user_input_text}</p>
            </div>

            {selected.ai_response_text && (
              <div className="vr-detail-block">
                <h3>對方回應</h3>
                <p>{selected.ai_response_text}</p>
              </div>
            )}

            <div className="vr-detail-score">
              <span>{`綜合分數：${selected.composite_score?.toFixed(4) ?? '—'}`}</span>
              <span>{`被引用次數：${selected.citation_count}`}</span>
              <span>{`目前狀態：${STATUS_LABELS[selected.review_status] ?? selected.review_status}`}</span>
            </div>

            <details className="vr-score-detail">
              <summary>評分細節</summary>
              <pre>{JSON.stringify(selected.score_detail, null, 2)}</pre>
            </details>

            <label className="vr-notes-label" htmlFor="vr-notes">
              審核備註（選填）
            </label>
            <textarea
              id="vr-notes"
              className="vr-notes-input"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              rows={3}
            />

            {actionError && <div className="vr-action-error">{actionError}</div>}

            <div className="vr-action-row">
              {selected.review_status === 'pending' ? (
                <>
                  <button
                    type="button"
                    className="vr-approve-btn"
                    disabled={isSubmitting}
                    onClick={() => handleDecision('approve')}
                  >
                    通過
                  </button>
                  <button
                    type="button"
                    className="vr-reject-btn"
                    disabled={isSubmitting}
                    onClick={() => handleDecision('reject')}
                  >
                    未通過
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="vr-reset-btn"
                  disabled={isSubmitting}
                  onClick={() => handleDecision('reset')}
                >
                  重新審查
                </button>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

export default ViewpointReviewPage;
