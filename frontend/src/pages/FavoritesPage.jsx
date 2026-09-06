import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useFavorites } from '../hooks/useFavorites';
import FavoriteStarButton from '../components/FavoriteStarButton';
import { prefetchConversation } from './kbConversationCache';
import '../pages/KnowledgeBase.css';
import './FavoritesPage.css';

const STANCE_LABELS = {
  pro: '支持',
  support: '支持',
  con: '反對',
  oppose: '反對',
  against: '反對',
  neutral: '中立',
};

const SPEAKER_SIDE_LABELS = { a: 'A方', b: 'B方' };

const FavoritesPage = () => {
  const navigate = useNavigate();

  const {
    favoriteItems: favoriteViewpoints,
    isFavorited: isViewpointFavorited,
    toggleFavorite: toggleViewpointFavorite,
    isLoading: viewpointsLoading,
  } = useFavorites('viewpoint');
  const {
    favoriteItems: favoriteVideos,
    isFavorited: isVideoFavorited,
    toggleFavorite: toggleVideoFavorite,
    isLoading: videosLoading,
  } = useFavorites('video');

  const goToConversation = (viewpoint) => {
    navigate(`/kb/conversations/${viewpoint.id}`);
  };

  return (
    <div className="kb-page">
      <div className="kb-section-title">我的收藏</div>

      <section className="kb-highlights-section">
        <div className="kb-section-title">收藏的觀點</div>

        {viewpointsLoading ? (
          <div className="kb-empty-hint">載入中…</div>
        ) : favoriteViewpoints.length === 0 ? (
          <div className="kb-empty-hint">還沒有收藏任何觀點，去觀點知識庫逛逛吧。</div>
        ) : (
          <div className="kb-highlights-grid">
            {favoriteViewpoints.map((viewpoint) => (
              <div
                key={viewpoint.id}
                className="kb-highlight-card"
                onClick={() => goToConversation(viewpoint)}
                onMouseEnter={() => prefetchConversation(viewpoint.id)}
                onMouseDown={() => prefetchConversation(viewpoint.id)}
              >
                <div className="kb-highlight-meta">
                  {viewpoint.topic_title && (
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
                <div className="kb-highlight-footer">
                  <span>被收藏 {viewpoint.favorite_count} 次</span>
                  <FavoriteStarButton
                    active={isViewpointFavorited(viewpoint.id)}
                    onToggle={() => toggleViewpointFavorite(viewpoint)}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="kb-video-section">
        <div className="kb-section-title">收藏的影片</div>

        {videosLoading ? (
          <div className="kb-empty-hint">載入中…</div>
        ) : favoriteVideos.length === 0 ? (
          <div className="kb-empty-hint">還沒有收藏任何影片。</div>
        ) : (
          <div className="kb-video-grid">
            {favoriteVideos.map((video) => (
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
  );
};

export default FavoritesPage;
