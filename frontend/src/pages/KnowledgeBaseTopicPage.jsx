import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import { getDialogueTopics } from '../api/dialogueTopics';
import { BROWSE_FETCH_SIZE, CARDS_PER_PAGE, browseCache, browseCacheKey } from './kbBrowseCache';
import { prefetchConversation } from './kbConversationCache';
import './KnowledgeBase.css';

// 對應 KnowledgeBase.jsx 的 ALL_TOPIC_ID：從首頁「全部」分類點「觀看更多」
// 進來時，路由參數會是這個字串，不對應任何真實 topic_id。
const ALL_TOPIC_ID = 'all';

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
// 注意：分組只在「目前這一頁」內做——分頁本身還是照每頁 PAGE_SIZE 筆
// 觀點算，不是每頁 PAGE_SIZE 場對話，同一場對話的觀點如果剛好被分頁切開，
// 還是會分別出現在不同頁。
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
  const location = useLocation();

  const [page, setPage] = useState(1);
  const [results, setResults] = useState(() => browseCache.get(browseCacheKey(topicId)) ?? []);
  const [loaded, setLoaded] = useState(() => browseCache.has(browseCacheKey(topicId)));
  // 首頁「觀看更多」按鈕會把標題塞在 navigation state 裡，有的話就直接用、
  // 省掉一支 /api/dialogue/topics/ 請求；深連結／重新整理時 state 為空才去抓。
  const [topicTitle, setTopicTitle] = useState(location.state?.topicTitle || '');
  // 同一場對話分組後預設折疊，只顯示分數最高的第一筆；點箭頭才展開看其他筆。
  const [expandedGroupIds, setExpandedGroupIds] = useState(() => new Set());

  // 換議題時（React Router 會沿用同一個元件實例，不是重新掛載）在 render 階段
  // 就把 results / loaded / page 對齊新議題——這是 React 官方「prop 變了就重置
  // state」的寫法，不放進 effect 以免多一輪 render。
  const [trackedTopicId, setTrackedTopicId] = useState(topicId);
  if (topicId !== trackedTopicId) {
    setTrackedTopicId(topicId);
    setResults(browseCache.get(browseCacheKey(topicId)) ?? []);
    setLoaded(browseCache.has(browseCacheKey(topicId)));
    setPage(1);
  }

  useEffect(() => {
    if (topicId === ALL_TOPIC_ID || location.state?.topicTitle) return undefined;

    let cancelled = false;

    getDialogueTopics().then((list) => {
      if (cancelled) return;
      const match = list.find((topic) => String(topic.id) === String(topicId));
      if (match) setTopicTitle(match.title);
    });

    return () => {
      cancelled = true;
    };
  }, [topicId, location.state]);

  // 一次抓回整個議題的觀點，背景刷新快取。翻頁不再打 API，由前端依「卡片」
  // 切頁（見下方 visibleGroups）。快取命中時上面 render 階段已經先把畫面填好，
  // 這裡只負責把資料抓新。
  useEffect(() => {
    let cancelled = false;

    // "全部"不帶 topic_id：browse 端點本來就支援跨議題（見後端
    // KnowledgeBaseViewpointBrowseView docstring）。
    const topicParams = topicId === ALL_TOPIC_ID ? {} : { topic_id: topicId };
    const key = browseCacheKey(topicId);

    api
      .get('/api/summary/viewpoints/browse/', {
        params: { ...topicParams, page: 1, page_size: BROWSE_FETCH_SIZE },
      })
      .then((response) => {
        const rows = Array.isArray(response.data?.results) ? response.data.results : [];
        browseCache.set(key, rows);
        if (!cancelled) {
          setResults(rows);
          setLoaded(true);
        }
      })
      .catch(() => {
        // 背景重抓失敗時，有舊快取就繼續沿用、不要清空畫面。
        if (!cancelled && !browseCache.has(key)) {
          setResults([]);
          setLoaded(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [topicId]);

  const isAllTopics = topicId === ALL_TOPIC_ID;
  // "全部"底下結果橫跨多個議題，不能像單一議題那樣借用第一筆結果的
  // topic_title 當標題。
  const displayTitle = isAllTopics ? '全部' : topicTitle || results[0]?.topic_title || '';

  // 同一場對話分組成卡片後，依卡片數切頁：每頁固定 CARDS_PER_PAGE 張，
  // 只有最後一頁可能不足。
  const allGroups = useMemo(() => groupViewpointsBySummary(results), [results]);
  const totalPages = Math.max(1, Math.ceil(allGroups.length / CARDS_PER_PAGE));
  const safePage = Math.min(page, totalPages);
  const visibleGroups = allGroups.slice(
    (safePage - 1) * CARDS_PER_PAGE,
    safePage * CARDS_PER_PAGE,
  );

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
        {allGroups.length > 0 && (
          <span className="kb-browse-count"> 共 {allGroups.length} 場對話</span>
        )}
      </div>

      {!loaded && results.length === 0 ? (
        <div className="kb-empty-hint">載入中…</div>
      ) : loaded && results.length === 0 ? (
        <div className="kb-empty-hint">
          {isAllTopics ? '目前還沒有通過審核的觀點內容' : '這個議題目前還沒有通過審核的觀點內容'}
        </div>
      ) : (
        <div className="kb-highlights-grid">
          {visibleGroups.map((group) => {
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
              onMouseEnter={() => prefetchConversation(group.viewpoints[0].id)}
              onMouseDown={() => prefetchConversation(group.viewpoints[0].id)}
            >
              {visibleViewpoints.map((viewpoint) => (
                <div key={viewpoint.id} className="kb-highlight-group-item">
                  <div className="kb-highlight-meta">
                    {isAllTopics && viewpoint.topic_title && (
                      <span className="kb-highlight-topic">{viewpoint.topic_title}</span>
                    )}
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
                  <div className="kb-highlight-footer">被收藏 {viewpoint.favorite_count} 次</div>
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
            disabled={safePage <= 1}
            onClick={() => setPage(Math.max(1, safePage - 1))}
          >
            上一頁
          </button>
          <span>
            第 {safePage} / {totalPages} 頁
          </span>
          <button
            type="button"
            disabled={safePage >= totalPages}
            onClick={() => setPage(Math.min(totalPages, safePage + 1))}
          >
            下一頁
          </button>
        </div>
      )}
    </div>
  );
};

export default KnowledgeBaseTopicPage;
