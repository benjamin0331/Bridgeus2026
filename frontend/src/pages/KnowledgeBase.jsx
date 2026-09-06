import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { getDialogueTopics } from '../api/dialogueTopics';
import { useFavorites } from '../hooks/useFavorites';
import FavoriteStarButton from '../components/FavoriteStarButton';
import { BROWSE_FETCH_SIZE, browseCache, browseCacheKey } from './kbBrowseCache';
import { prefetchConversation } from './kbConversationCache';
import {
  ALL_TOPIC_ID,
  fetchHighlights,
  fetchVideos,
  highlightsCache,
  prefetchHighlights,
  prefetchVideos,
  videosCache,
} from './kbHomeCache';
import './KnowledgeBase.css';

// "全部"是前端合成的分類，不對應任何真實 topic_id——選到它時打後端 API 一律
// 不帶 topic_id（三支端點原本就支援跨全部議題）。ALL_TOPIC 只是給 UI 用的
// {id,title}，實際打 API 的參數處理在 kbHomeCache。
const ALL_TOPIC = { id: ALL_TOPIC_ID, title: '全部' };

// browse（「觀看更多」整份清單）預抓用；highlights/videos 走 kbHomeCache。
const browseParamsFor = (id) => (id === ALL_TOPIC_ID ? {} : { topic_id: id });

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
  // 預設選「全部」，一進頁面就看得到跨議題的熱門對話/影片，不用先選一個
  // 議題才有東西看。
  const [selectedTopicId, setSelectedTopicId] = useState(ALL_TOPIC_ID);
  const [topConversations, setTopConversations] = useState([]);
  const [topConversationsLoaded, setTopConversationsLoaded] = useState(false);
  const [videos, setVideos] = useState([]);
  const [videosLoaded, setVideosLoaded] = useState(false);

  const { isFavorited: isViewpointFavorited, toggleFavorite: toggleViewpointFavorite } = useFavorites('viewpoint');
  const { isFavorited: isVideoFavorited, toggleFavorite: toggleVideoFavorite } = useFavorites('video');
  // 熱門對話 / 影片的快取在 kbHomeCache（模組層），首頁就能先預抓。這裡只留
  // 一個旗標，避免重複跑「背景預抓其他分類」。
  const prefetchedRef = useRef(false);
  // 同一場對話分組後預設折疊，只顯示分數最高的第一筆；點箭頭才展開看其他筆。
  const [expandedGroupIds, setExpandedGroupIds] = useState(() => new Set());
  // 點影片卡片直接在站內彈窗播放，不再另開分頁——後端 media_views.serve_media
  // 已經支援 HTTP Range，<video> 原生播放器才能正常拖時間軸/讀到音軌。
  const [playingVideo, setPlayingVideo] = useState(null);

  useEffect(() => {
    let cancelled = false;

    getDialogueTopics().then((list) => {
      if (cancelled) return;
      setTopics(list);
      setTopicsLoaded(true);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  // 選定議題後拉「熱門對話」小卡（預設 TOP_CONVERSATIONS_LIMIT 筆）和影片推薦。
  //
  // 切分類先秀快取、再背景重抓：換分類時畫面立即更新，不用等網路來回。
  //
  // 額外監聽 visibilitychange/focus：研究者常見的操作路徑是在另一個分頁
  // （或先切走）把觀點審核通過，再切回這個已經開著、議題沒變的知識庫分頁
  // ——這種情況 selectedTopicId 沒變，effect 的依賴陣列不會重新觸發，畫面
  // 會停在審核通過「之前」抓到的舊資料。分頁重新取得焦點時再背景重抓一次。
  useEffect(() => {
    let cancelled = false;
    const key = String(selectedTopicId);

    const loadForTopic = () => {
      const cachedHighlights = highlightsCache.get(key);
      const cachedVideos = videosCache.get(key);

      // 有快取就立刻顯示（不轉圈），沒有才顯示載入中。
      if (cachedHighlights) {
        setTopConversations(cachedHighlights);
        setTopConversationsLoaded(true);
      } else {
        setTopConversationsLoaded(false);
      }
      if (cachedVideos) {
        setVideos(cachedVideos);
        setVideosLoaded(true);
      } else {
        setVideosLoaded(false);
      }

      fetchHighlights(selectedTopicId)
        .then((rows) => {
          if (!cancelled) {
            setTopConversations(rows);
            setTopConversationsLoaded(true);
          }
        })
        .catch(() => {
          // 重抓失敗時，有舊快取就繼續沿用、不要清空畫面。
          if (!cancelled && !cachedHighlights) {
            setTopConversations([]);
            setTopConversationsLoaded(true);
          }
        });

      fetchVideos(selectedTopicId)
        .then((rows) => {
          if (!cancelled) {
            setVideos(rows);
            setVideosLoaded(true);
          }
        })
        .catch(() => {
          if (!cancelled && !cachedVideos) {
            setVideos([]);
            setVideosLoaded(true);
          }
        });

      // 順手預抓「觀看更多」整個清單，讓點按鈕進去秒開（結果存進跨頁共用的
      // browseCache，KnowledgeBaseTopicPage 會先讀它）。
      const browseKey = browseCacheKey(selectedTopicId);
      if (!browseCache.has(browseKey)) {
        api
          .get('/api/summary/viewpoints/browse/', {
            params: { ...browseParamsFor(selectedTopicId), page: 1, page_size: BROWSE_FETCH_SIZE },
          })
          .then((response) => {
            browseCache.set(
              browseKey,
              Array.isArray(response.data?.results) ? response.data.results : [],
            );
          })
          .catch(() => {});
      }
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

  // 議題清單載入後，背景預抓其他分類（目前選的那個由上面的 effect 負責），
  // 之後點任何分類都直接命中快取。分類只有 3～4 個，一次性的少量請求。
  useEffect(() => {
    if (!topicsLoaded || prefetchedRef.current) return;
    prefetchedRef.current = true;

    [ALL_TOPIC_ID, ...topics.map((topic) => topic.id)]
      .filter((id) => String(id) !== String(selectedTopicId))
      .forEach((id) => {
        prefetchHighlights(id);
        prefetchVideos(id);
      });
  }, [topicsLoaded, topics, selectedTopicId]);


  const groupedTopConversations = useMemo(
    () => groupViewpointsBySummary(topConversations),
    [topConversations],
  );

  const selectedTopic =
    selectedTopicId === ALL_TOPIC_ID
      ? ALL_TOPIC
      : topics.find((topic) => topic.id === selectedTopicId) ?? null;

  // "全部"視圖底下卡片會混著不同議題，每張卡片要標出自己的議題名稱才看得懂
  // ——觀點卡片後端已經回傳 topic_title（見 _serialize_viewpoint_rows），
  // 影片只回傳 topic_id，前端自己用已載入的議題清單查表。
  const topicTitleById = useMemo(() => {
    const map = new Map();
    topics.forEach((topic) => map.set(topic.id, topic.title));
    return map;
  }, [topics]);
  const isAllTopics = selectedTopicId === ALL_TOPIC_ID;

  const goToConversation = (viewpoint) => {
    navigate(`/kb/conversations/${viewpoint.id}`);
  };

  const selectTopic = (topic) => {
    setSelectedTopicId(topic.id);
  };

  // 記一筆觀看紀錄給後端的影片推薦演算法用（初期熱門排序／後期相反立場
  // 推薦都要靠這份紀錄才能算比例，見 VideoRecommendationListView）。播放
  // 是本地互動，不因為記錄失敗就卡住，所以不 await、失敗也不特別處理。
  // "全部"視圖底下沒有單一在瀏覽的議題，改記這部影片自己的 topic_id
  // （VideoWatchEventCreateView 要求 topic_id 必須是真的整數）；影片本身
  // 若是「不限議題」的共用推薦（topic_id 為 null），就沒有可歸屬的議題，
  // 略過不記——跟該端點原本「無法判斷議題就不記」的精神一致。
  const openVideo = (video) => {
    setPlayingVideo(video);
    const watchTopicId = isAllTopics ? video.topic_id : selectedTopicId;
    if (watchTopicId) {
      api.post(`/api/summary/videos/${video.id}/watch/`, { topic_id: watchTopicId }).catch(() => {});
    }
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
        <button type="button" className="kb-back-btn" onClick={() => navigate('/')}>
          <img src="/back-arrow.svg" alt="" className="kb-back-icon" />
          返回首頁
        </button>

        {/* 議題選擇 */}
        <div className="kb-topic-select">
          <div className="kb-section-title">選擇議題</div>
          <div className="kb-tag-cloud">
            {/* "全部"永遠排第一個、永遠可選——它不是從後端議題清單來的，
                不受「目前沒有開放中的議題」影響。 */}
            <div
              className={`kb-tag${isAllTopics ? ' kb-tag-active' : ''}`}
              onClick={() => selectTopic(ALL_TOPIC)}
            >
              {ALL_TOPIC.title}
            </div>
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

        {/* 熱門對話小卡：每個分類預設顯示 TOP_CONVERSATIONS_LIMIT 筆 */}
        <section className="kb-highlights-section">
          <div className="kb-section-title">
            {isAllTopics ? '全部議題熱門對話' : `「${selectedTopic.title}」熱門對話`}
          </div>

          {topConversationsLoaded && topConversations.length === 0 ? (
            <div className="kb-empty-hint">
              {isAllTopics ? '目前還沒有通過審核的觀點內容' : '這個議題目前還沒有通過審核的觀點內容'}
            </div>
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
                      <div className="kb-highlight-footer">
                        <span>被收藏 {viewpoint.favorite_count} 次</span>
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

          {topConversations.length > 0 && (
            <button
              type="button"
              className="kb-view-more-btn"
              onClick={() =>
                navigate(`/kb/topics/${selectedTopicId}`, {
                  state: {
                    topicTitle: isAllTopics ? '全部' : selectedTopic.title,
                  },
                })
              }
            >
              觀看更多
            </button>
          )}
        </section>

        {/* 影片推薦：內容由後台（Django admin）人工維護 */}
        <section className="kb-video-section">
          <div className="kb-section-title">影片推薦</div>
          {videosLoaded && videos.length === 0 ? (
            <div className="kb-empty-hint">
              {isAllTopics ? '目前還沒有任何推薦影片' : '目前還沒有這個議題的推薦影片'}
            </div>
          ) : (
            <div className="kb-video-grid">
              {videos.map((video) => (
                <button
                  key={video.id}
                  type="button"
                  className="kb-video-card"
                  onClick={() => openVideo(video)}
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
                  {isAllTopics && topicTitleById.get(video.topic_id) && (
                    <span className="kb-video-topic">{topicTitleById.get(video.topic_id)}</span>
                  )}
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
