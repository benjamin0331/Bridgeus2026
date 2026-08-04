import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { useFavorites } from '../hooks/useFavorites';
import FavoriteStarButton from '../components/FavoriteStarButton';
import './KnowledgeBase.css';

const TRENDING_DISPLAY_LIMIT = 4;
const TOP_CONVERSATIONS_LIMIT = 5;
const PIE_COLORS = ['#748ba7', '#5d7594', '#8ca2bc', '#a9bad0'];

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

// 議題活動量沒有固定總量，圓餅圖比例直接從當下抓到的 hits 佔比換算。
function buildPieGradient(items) {
  const total = items.reduce((sum, item) => sum + item.hits, 0);
  if (total <= 0) {
    return '#d8e4f2';
  }

  let cursor = 0;
  const stops = items.map((item, index) => {
    const start = cursor;
    cursor += (item.hits / total) * 100;
    const color = PIE_COLORS[index % PIE_COLORS.length];
    return `${color} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
  });

  return `conic-gradient(${stops.join(', ')})`;
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

    return () => {
      cancelled = true;
    };
  }, [selectedTopicId]);

  const topTrending = trending.slice(0, TRENDING_DISPLAY_LIMIT);

  const selectedTopic = useMemo(
    () => topics.find((topic) => topic.id === selectedTopicId) ?? null,
    [topics, selectedTopicId],
  );

  const goToTopic = (topic) => {
    navigate(`/topic/${topic.id}?mode=ai`);
  };

  const goToConversation = (viewpoint) => {
    navigate(`/kb/conversations/${viewpoint.id}`);
  };

  const selectTopic = (topic) => {
    setSelectedTopicId(topic.id);
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
              {topConversations.map((viewpoint) => (
                <div
                  key={viewpoint.id}
                  className="kb-highlight-card"
                  onClick={() => goToConversation(viewpoint)}
                >
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
                  <div className="kb-highlight-footer">
                    <span>被引用 {viewpoint.citation_count} 次</span>
                    <FavoriteStarButton
                      active={isViewpointFavorited(viewpoint.id)}
                      onToggle={() => toggleViewpointFavorite(viewpoint)}
                    />
                  </div>
                </div>
              ))}
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
                <a
                  key={video.id}
                  className="kb-video-card"
                  href={video.url}
                  target="_blank"
                  rel="noopener noreferrer"
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
                </a>
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
                <div key={item.id} className="kb-trending-item" onClick={() => goToTopic(item)}>
                  <span>{item.title}</span>
                  <span>{item.hits}/Hits</span>
                </div>
              ))
            )}
          </div>

          <div className="kb-chart-placeholder">
            <div
              className="dummy-pie-chart"
              style={{ background: buildPieGradient(topTrending) }}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default KnowledgeBase;
