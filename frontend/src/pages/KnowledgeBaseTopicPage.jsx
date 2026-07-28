import React, { useState, useEffect } from 'react';
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

const KnowledgeBaseTopicPage = () => {
  const { topicId } = useParams();
  const navigate = useNavigate();

  const [page, setPage] = useState(1);
  const [results, setResults] = useState([]);
  const [count, setCount] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [topicTitle, setTopicTitle] = useState('');

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

    const loadPage = async () => {
      setLoaded(false);

      await api
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

    void loadPage();

    return () => {
      cancelled = true;
    };
  }, [topicId, page]);

  const displayTitle = topicTitle || results[0]?.topic_title || '';
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));

  const goToConversation = (viewpoint) => {
    navigate(`/kb/conversations/${viewpoint.id}`);
  };

  return (
    <div className="kb-page">
      <button type="button" className="kb-back-btn" onClick={() => navigate('/kb')}>
        ← 返回知識庫
      </button>

      <div className="kb-section-title">
        {displayTitle ? `「${displayTitle}」所有收錄對話` : '所有收錄對話'}
        {count > 0 && <span className="kb-browse-count"> 共 {count} 筆</span>}
      </div>

      {loaded && results.length === 0 ? (
        <div className="kb-empty-hint">這個議題目前還沒有通過審核的觀點內容</div>
      ) : (
        <div className="kb-highlights-grid">
          {results.map((viewpoint) => (
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
              <div className="kb-highlight-footer">被引用 {viewpoint.citation_count} 次</div>
            </div>
          ))}
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
