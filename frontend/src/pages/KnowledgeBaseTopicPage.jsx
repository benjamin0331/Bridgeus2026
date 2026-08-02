import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import './KnowledgeBase.css';

const PAGE_SIZE = 12;

const STANCE_LABELS = {
  pro: '支持',
  support: '支持',
  con: '反對',
  oppose: '反對',
  against: '反對',
  neutral: '中立',
};

const SPEAKER_SIDE_LABELS = { a: 'A方', b: 'B方' };

// 同一場對話（同一個聊天室）常常會產生好幾筆觀點，依 dialogue_summary_id
// 分組後一起顯示，而不是打散成看起來互不相關的獨立卡片。保留原本依分數
// 排序後的先後順序（Map 的插入順序 = 第一次出現該 summary_id 的順序）。
// 注意：分組只在「目前這一頁」內做——分頁本身還是照原本每頁 12 筆觀點
// 算，不是每頁 12 場對話，同一場對話的觀點如果剛好被分頁切開，還是會
// 分別出現在不同頁。
function groupViewpointsBySummary(viewpoints) {
  const groups = [];
  const bySummaryId = new Map();
  viewpoints.forEach((viewpoint) => {
    const key = viewpoint.dialogue_summary_id;
    let group = bySummaryId.get(key);
    if (!group) {
      group = { summaryId: key, viewpoints: [] };
      bySummaryId.set(key, group);
      groups.push(group);
    }
    group.viewpoints.push(viewpoint);
  });
  return groups;
}

const KnowledgeBaseTopicPage = () => {
  const { topicId } = useParams();
  const navigate = useNavigate();

  const [page, setPage] = useState(1);
  const [results, setResults] = useState([]);
  const [count, setCount] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [topicTitle, setTopicTitle] = useState('');
  // 同一場對話分組後預設折疊，只顯示分數最高的第一筆；點箭頭才展開看其他筆。
  const [expandedGroupIds, setExpandedGroupIds] = useState(() => new Set());

  useEffect(() => {
    let cancelled = false;

    api
      .get('/api/dialogue/topics/')
      .then((response) => {
        if (cancelled) return;
        const topics = Array.isArray(response.data) ? response.data : [];
        const match = topics.find((topic) => String(topic.id) === String(topicId));
        if (match) setTopicTitle(match.title);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [topicId]);

  useEffect(() => {
    let cancelled = false;

    const loadPage = () => {
      setLoaded(false);

      api
        .get('/api/summary/viewpoints/browse/', {
          params: { topic_id: topicId, page, page_size: PAGE_SIZE },
        })
        .then((response) => {
          if (cancelled) return;
          setResults(Array.isArray(response.data?.results) ? response.data.results : []);
          setCount(response.data?.count ?? 0);
        })
        .catch(() => {
          if (!cancelled) {
            setResults([]);
            setCount(0);
          }
        })
        .finally(() => {
          if (!cancelled) setLoaded(true);
        });
    };

    loadPage();

    return () => {
      cancelled = true;
    };
  }, [topicId, page]);

  const displayTitle = topicTitle || results[0]?.topic_title || '';
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const groupedResults = useMemo(() => groupViewpointsBySummary(results), [results]);

  const goToConversation = (viewpoint) => {
    navigate(`/kb/conversations/${viewpoint.id}`);
  };

  // stopPropagation：箭頭包在會導到對話詳情頁的卡片裡，點箭頭只該展開/
  // 收合，不該連帶觸發卡片本身的導頁。
  const toggleGroupExpanded = (event, summaryId) => {
    event.stopPropagation();
    setExpandedGroupIds((current) => {
      const next = new Set(current);
      if (next.has(summaryId)) next.delete(summaryId);
      else next.add(summaryId);
      return next;
    });
  };

  // 分組卡片（有多筆觀點時）點兩下展開/收合，點一下才導頁：瀏覽器的
  // click 一定會先於 dblclick 觸發，所以單擊先延遲一小段時間才真的導頁，
  // 如果這段時間內又點了第二下，就取消導頁、改成觸發展開/收合，不會兩個
  // 動作打架（先跳頁又觸發展開）。只有 1 筆觀點的卡片沒有東西可以展開，
  // 維持點一下就直接導頁。
  const pendingCardClickRef = useRef(null);

  const handleGroupCardClick = (group, hasMultiple) => {
    if (!hasMultiple) {
      goToConversation(group.viewpoints[0]);
      return;
    }
    if (pendingCardClickRef.current) clearTimeout(pendingCardClickRef.current);
    pendingCardClickRef.current = setTimeout(() => {
      pendingCardClickRef.current = null;
      goToConversation(group.viewpoints[0]);
    }, 250);
  };

  const handleGroupCardDoubleClick = (event, group, hasMultiple) => {
    if (!hasMultiple) return;
    if (pendingCardClickRef.current) {
      clearTimeout(pendingCardClickRef.current);
      pendingCardClickRef.current = null;
    }
    toggleGroupExpanded(event, group.summaryId);
  };

  return (
    <div className="kb-page">
      <button type="button" className="kb-back-btn" onClick={() => navigate('/kb')}>
        <img src="/back-arrow.svg" alt="" className="kb-back-icon" />
        返回知識庫
      </button>

      <div className="kb-section-title">
        {displayTitle ? `「${displayTitle}」所有收錄對話` : '所有收錄對話'}
        {count > 0 && <span className="kb-browse-count"> 共 {count} 筆</span>}
      </div>

      {loaded && results.length === 0 ? (
        <div className="kb-empty-hint">這個議題目前還沒有通過審核的觀點內容</div>
      ) : (
        <div className="kb-highlights-grid">
          {groupedResults.map((group) => {
            const hasMultiple = group.viewpoints.length > 1;
            const isExpanded = expandedGroupIds.has(group.summaryId);
            const visibleViewpoints =
              hasMultiple && !isExpanded ? group.viewpoints.slice(0, 1) : group.viewpoints;
            return (
            <div
              key={group.summaryId}
              className="kb-highlight-group-card"
              onClick={() => handleGroupCardClick(group, hasMultiple)}
              onDoubleClick={(e) => handleGroupCardDoubleClick(e, group, hasMultiple)}
            >
              {visibleViewpoints.map((viewpoint) => (
                <div key={viewpoint.id} className="kb-highlight-group-item">
                  <div className="kb-highlight-meta">
                    {viewpoint.speaker_side && (
                      <span className="kb-highlight-side">
                        {SPEAKER_SIDE_LABELS[viewpoint.speaker_side] ?? viewpoint.speaker_side}
                      </span>
                    )}
                    <span className="kb-highlight-dimension">{viewpoint.dimension_name}</span>
                    {viewpoint.stance_direction && (
                      <span className="kb-highlight-stance">
                        {STANCE_LABELS[viewpoint.stance_direction] ?? viewpoint.stance_direction}
                      </span>
                    )}
                  </div>
                  <p className="kb-highlight-summary">
                    {viewpoint.viewpoint_summary || '（尚無摘要）'}
                  </p>
                  {viewpoint.user_input_text && (
                    <div className="kb-highlight-quote">
                      <div className="kb-highlight-quote-label">使用者發言</div>
                      <p className="kb-highlight-quote-text">{viewpoint.user_input_text}</p>
                    </div>
                  )}
                  {viewpoint.ai_response_text && (
                    <div className="kb-highlight-quote">
                      <div className="kb-highlight-quote-label">對方回應</div>
                      <p className="kb-highlight-quote-text">{viewpoint.ai_response_text}</p>
                    </div>
                  )}
                  <div className="kb-highlight-footer">被引用 {viewpoint.citation_count} 次</div>
                </div>
              ))}
              {hasMultiple && (
                <button
                  type="button"
                  className="kb-highlight-group-toggle"
                  onClick={(e) => toggleGroupExpanded(e, group.summaryId)}
                  aria-expanded={isExpanded}
                >
                  <span className="kb-highlight-group-badge">
                    同一場對話 · {group.viewpoints.length} 則觀點
                  </span>
                  <span className={`kb-highlight-group-arrow${isExpanded ? ' is-expanded' : ''}`}>
                    ▾
                  </span>
                </button>
              )}
            </div>
            );
          })}
        </div>
      )}

      {totalPages > 1 && (
        <div className="kb-pagination">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            上一頁
          </button>
          <span>
            第 {page} / {totalPages} 頁
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            下一頁
          </button>
        </div>
      )}
    </div>
  );
};

export default KnowledgeBaseTopicPage;
