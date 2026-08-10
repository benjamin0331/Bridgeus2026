import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { useFavorites } from '../hooks/useFavorites';
import FavoriteStarButton from '../components/FavoriteStarButton';
import './KnowledgeBase.css';

const TRENDING_DISPLAY_LIMIT = 4;
const TOP_CONVERSATIONS_LIMIT = 5;

// stance_direction 目前後端沒有固定的中文對照表，這裡只涵蓋已知會出現的值，
// 其餘（例如空字串）就照原樣顯示，不強行翻譯。
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

const KnowledgeBase = () => {
  const navigate = useNavigate();

  const [topics, setTopics] = useState([]);
  const [topicsLoaded, setTopicsLoaded] = useState(false);
  const [trending, setTrending] = useState([]);
  const [selectedTopicId, setSelectedTopicId] = useState(null);
  const [topConversations, setTopConversations] = useState([]);
  const [topConversationsLoaded, setTopConversationsLoaded] = useState(false);
  const [videos, setVideos] = useState([]);
  const [videosLoaded, setVideosLoaded] = useState(false);

  const { isFavorited: isViewpointFavorited, toggleFavorite: toggleViewpointFavorite } = useFavorites('viewpoint');
  const { isFavorited: isVideoFavorited, toggleFavorite: toggleVideoFavorite } = useFavorites('video');
  // 同一場對話分組後預設折疊，只顯示分數最高的第一筆；點箭頭才展開看其他筆。
  const [expandedGroupIds, setExpandedGroupIds] = useState(() => new Set());
  // 點影片卡片直接在站內彈窗播放，不再另開分頁——後端 media_views.serve_media
  // 已經支援 HTTP Range，<video> 原生播放器才能正常拖時間軸/讀到音軌。
  const [playingVideo, setPlayingVideo] = useState(null);

  useEffect(() => {
    let cancelled = false;

    api
      .get('/api/dialogue/topics/')
      .then((response) => {
        if (!cancelled) setTopics(Array.isArray(response.data) ? response.data : []);
      })
      .catch(() => {
        if (!cancelled) setTopics([]);
      })
      .finally(() => {
        if (!cancelled) setTopicsLoaded(true);
      });

    api
      .get('/api/dialogue/topics/trending/')
      .then((response) => {
        if (!cancelled) setTrending(Array.isArray(response.data) ? response.data : []);
      })
      .catch(() => {
        if (!cancelled) setTrending([]);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // 選定議題後才拉「熱門對話 Top 5」和過濾影片推薦。
  //
  // 額外監聽 visibilitychange/focus：研究者常見的操作路徑是在另一個分頁
  // （或先切走）把觀點審核通過，再切回這個已經開著、議題沒變的知識庫分頁
  // ——這種情況 selectedTopicId 沒變，effect 的依賴陣列不會重新觸發，畫面
  // 會停在審核通過「之前」抓到的舊資料，看起來就像「審核通過了但知識庫
  // 不會顯示」。分頁重新取得焦點時比照 selectedTopicId 變動再拉一次最新的。
  useEffect(() => {
    let cancelled = false;

    const loadForTopic = () => {
      if (!selectedTopicId) {
        setTopConversations([]);
        setTopConversationsLoaded(false);
        setVideos([]);
        setVideosLoaded(false);
        return;
      }

      setTopConversationsLoaded(false);
      setVideosLoaded(false);

      api
        .get('/api/summary/viewpoints/highlights/', {
          params: { topic_id: selectedTopicId, limit: TOP_CONVERSATIONS_LIMIT },
        })
        .then((response) => {
          if (!cancelled) setTopConversations(Array.isArray(response.data) ? response.data : []);
        })
        .catch(() => {
          if (!cancelled) setTopConversations([]);
        })
        .finally(() => {
          if (!cancelled) setTopConversationsLoaded(true);
        });

      api
        .get('/api/summary/videos/', { params: { topic_id: selectedTopicId } })
        .then((response) => {
          if (!cancelled) setVideos(Array.isArray(response.data) ? response.data : []);
        })
        .catch(() => {
          if (!cancelled) setVideos([]);
        })
        .finally(() => {
          if (!cancelled) setVideosLoaded(true);
        });
    };

    loadForTopic();

    const handleRefetchOnFocus = () => {
      if (document.visibilityState === 'visible') {
        loadForTopic();
      }
    };
    document.addEventListener('visibilitychange', handleRefetchOnFocus);
    window.addEventListener('focus', handleRefetchOnFocus);

    return () => {
      cancelled = true;
      document.removeEventListener('visibilitychange', handleRefetchOnFocus);
      window.removeEventListener('focus', handleRefetchOnFocus);
    };
  }, [selectedTopicId]);

  const topTrending = trending.slice(0, TRENDING_DISPLAY_LIMIT);

  const groupedTopConversations = useMemo(
    () => groupViewpointsBySummary(topConversations),
    [topConversations],
  );

  const selectedTopic = useMemo(
    () => topics.find((topic) => topic.id === selectedTopicId) ?? null,
    [topics, selectedTopicId],
  );

  const goToConversation = (viewpoint) => {
    navigate(`/kb/conversations/${viewpoint.id}`);
  };

  const selectTopic = (topic) => {
    setSelectedTopicId(topic.id);
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
    <div className="kb-container">
      <div className="kb-left-section">
        {/* 議題選擇 */}
        <div className="kb-topic-select">
          <div className="kb-section-title">選擇議題</div>
          <div className="kb-tag-cloud">
            {topicsLoaded && topics.length === 0 && (
              <div className="kb-empty-hint">目前沒有開放中的議題</div>
            )}
            {topics.map((topic) => (
              <div
                key={topic.id}
                className={`kb-tag${topic.id === selectedTopicId ? ' kb-tag-active' : ''}`}
                onClick={() => selectTopic(topic)}
              >
                {topic.title}
              </div>
            ))}
          </div>
        </div>

        {/* 熱門對話 Top 5：選定議題後才顯示 */}
        <section className="kb-highlights-section">
          <div className="kb-section-title">
            {selectedTopic ? `「${selectedTopic.title}」熱門對話 Top 5` : '熱門對話'}
          </div>

          {!selectedTopicId ? (
            <div className="kb-empty-hint">請先在上方選擇一個議題</div>
          ) : topConversationsLoaded && topConversations.length === 0 ? (
            <div className="kb-empty-hint">這個議題目前還沒有通過審核的觀點內容</div>
          ) : (
            <div className="kb-highlights-grid">
              {groupedTopConversations.map((group) => {
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
                      <div className="kb-highlight-footer">
                        <span>被引用 {viewpoint.citation_count} 次</span>
                        <FavoriteStarButton
                          active={isViewpointFavorited(viewpoint.id)}
                          onToggle={() => toggleViewpointFavorite(viewpoint)}
                        />
                      </div>
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

          {selectedTopicId && topConversations.length > 0 && (
            <button
              type="button"
              className="kb-view-more-btn"
              onClick={() => navigate(`/kb/topics/${selectedTopicId}`)}
            >
              觀看更多
            </button>
          )}
        </section>

        {/* 影片推薦：內容由後台（Django admin）人工維護 */}
        <section className="kb-video-section">
          <div className="kb-section-title">影片推薦</div>
          {!selectedTopicId ? (
            <div className="kb-empty-hint">請先在上方選擇一個議題</div>
          ) : videosLoaded && videos.length === 0 ? (
            <div className="kb-empty-hint">目前還沒有這個議題的推薦影片</div>
          ) : (
            <div className="kb-video-grid">
              {videos.map((video) => (
                <button
                  key={video.id}
                  type="button"
                  className="kb-video-card"
                  onClick={() => setPlayingVideo(video)}
                >
                  <div
                    className="kb-video-thumb"
                    style={
                      video.thumbnail_url
                        ? { backgroundImage: `url(${video.thumbnail_url})` }
                        : undefined
                    }
                  >
                    <FavoriteStarButton
                      className="favorite-star-btn--overlay"
                      active={isVideoFavorited(video.id)}
                      onToggle={() => toggleVideoFavorite(video)}
                    />
                  </div>
                  <div className="kb-video-title">{video.title}</div>
                  {video.description && (
                    <div className="kb-video-description">{video.description}</div>
                  )}
                </button>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* 近期熱門：跨議題的整體活動量統計 */}
      <div className="kb-right-section">
        <div className="kb-blue-card">
          <h3>近期熱門</h3>
          <div className="kb-trending-list">
            {topTrending.length === 0 ? (
              <div className="kb-history-empty">尚無活動資料</div>
            ) : (
              topTrending.map((item) => (
                <div
                  key={item.id}
                  className={`kb-trending-item${item.id === selectedTopicId ? ' kb-trending-item-active' : ''}`}
                  onClick={() => selectTopic(item)}
                >
                  <span>{item.title}</span>
                  <span>{item.hits}/Hits</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {playingVideo && (
        <div className="kb-video-modal-backdrop" onClick={() => setPlayingVideo(null)}>
          <div className="kb-video-modal" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="kb-video-modal-close"
              onClick={() => setPlayingVideo(null)}
              aria-label="關閉"
            >
              ×
            </button>
            {/* 沒有 <track> 字幕：影片是研究者自行上傳的既有素材，平台沒有
                字幕檔來源，也沒有轉錄管線。本專案的 eslint 設定未啟用
                jsx-a11y，所以這裡不需要 eslint-disable；若之後導入該 plugin，
                media-has-caption 會需要在這裡補一行豁免。 */}
            <video
              className="kb-video-modal-player"
              src={playingVideo.url}
              controls
              autoPlay
            />
            <div className="kb-video-modal-title">{playingVideo.title}</div>
            {playingVideo.description && (
              <div className="kb-video-modal-description">{playingVideo.description}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default KnowledgeBase;
