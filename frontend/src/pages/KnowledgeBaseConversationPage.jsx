import React, { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import './KnowledgeBase.css';

const STANCE_LABELS = {
  pro: '支持',
  support: '支持',
  con: '反對',
  oppose: '反對',
  against: '反對',
  neutral: '中立',
};

const SPEAKER_SIDE_LABELS = { a: 'A方', b: 'B方' };

function formatStance(value) {
  if (!value) return '未標記';
  return STANCE_LABELS[value] ?? value;
}

const KnowledgeBaseConversationPage = () => {
  const { viewpointId } = useParams();
  const navigate = useNavigate();

  const [conversation, setConversation] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    setError(false);

    api
      .get(`/api/summary/viewpoints/${viewpointId}/conversation/`)
      .then((response) => {
        if (!cancelled) setConversation(response.data ?? null);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });

    return () => {
      cancelled = true;
    };
  }, [viewpointId]);

  return (
    <div className="kb-page">
      <button type="button" className="kb-back-btn" onClick={() => navigate(-1)}>
        ← 返回
      </button>

      {!loaded ? (
        <div className="kb-empty-hint">載入中…</div>
      ) : error || !conversation ? (
        <div className="kb-empty-hint">找不到這筆對話紀錄，或尚未通過審核。</div>
      ) : (
        <>
          <div className="kb-section-title">{conversation.topic_title}</div>

          <div className="kb-conversation-card">
            <div className="kb-conversation-stances">
              <span className="kb-highlight-stance">A：{formatStance(conversation.side_a_stance)}</span>
              <span className="kb-highlight-stance">B：{formatStance(conversation.side_b_stance)}</span>
            </div>

            <p className="kb-conversation-summary">
              {conversation.summary_text || '（尚無摘要）'}
            </p>

            <div className="kb-conversation-metrics">
              {conversation.quality_score != null && (
                <span>品質分數：{conversation.quality_score.toFixed(2)}</span>
              )}
              {conversation.stance_shift_magnitude != null && (
                <span>立場偏移量：{conversation.stance_shift_magnitude.toFixed(2)}</span>
              )}
            </div>
          </div>

          <div className="kb-section-title">這場對話收錄的觀點</div>
          <div className="kb-highlights-grid">
            {conversation.viewpoints.map((viewpoint) => (
              <div key={viewpoint.id} className="kb-highlight-card">
                <div className="kb-highlight-meta">
                  {viewpoint.speaker_side && (
                    <span className="kb-highlight-side">
                      {SPEAKER_SIDE_LABELS[viewpoint.speaker_side] ?? viewpoint.speaker_side}
                    </span>
                  )}
                  <span className="kb-highlight-dimension">{viewpoint.dimension_name}</span>
                  {viewpoint.stance_direction && (
                    <span className="kb-highlight-stance">
                      {formatStance(viewpoint.stance_direction)}
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
        </>
      )}
    </div>
  );
};

export default KnowledgeBaseConversationPage;
