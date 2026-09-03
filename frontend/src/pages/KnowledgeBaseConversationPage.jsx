import React, { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/client';
import ConversationTreePanel from '../components/ConversationTreePanel';
import './KnowledgeBase.css';

const STANCE_LABELS = {
  pro: '支持',
  support: '支持',
  con: '反對',
  oppose: '反對',
  against: '反對',
  neutral: '中立',
};

function formatStance(value) {
  if (!value) return '未標記';
  return STANCE_LABELS[value] ?? value;
}

function formatTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-TW', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function messageRowClass(side) {
  return side === 'b' ? 'kb-conversation-message-row is-side-b' : 'kb-conversation-message-row';
}

const KnowledgeBaseConversationPage = () => {
  const { viewpointId } = useParams();
  const navigate = useNavigate();

  const [conversation, setConversation] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const loadConversation = () => {
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
    };

    loadConversation();

    return () => {
      cancelled = true;
    };
  }, [viewpointId]);

  return (
    <div className="kb-page">
      <button type="button" className="kb-back-btn" onClick={() => navigate(-1)}>
        <img src="/back-arrow.svg" alt="" className="kb-back-icon" />
        返回
      </button>

      {!loaded ? (
        <div className="kb-empty-hint">載入中…</div>
      ) : error || !conversation ? (
        <div className="kb-empty-hint">找不到這筆對話紀錄，或尚未通過審核。</div>
      ) : (
        <div className="kb-conversation-layout">
          <section className="kb-conversation-message-panel">
            <div className="kb-conversation-header">
              <span className="kb-conversation-kicker">已通過審核的真人對話</span>
              <h2>{conversation.topic_title}</h2>
              <div className="kb-conversation-stances">
                <span className="kb-highlight-stance">A：{formatStance(conversation.side_a_stance)}</span>
                <span className="kb-highlight-stance">B：{formatStance(conversation.side_b_stance)}</span>
                {conversation.quality_score != null && (
                  <span className="kb-conversation-metric">品質分數：{conversation.quality_score.toFixed(2)}</span>
                )}
                {conversation.stance_shift_magnitude != null && (
                  <span className="kb-conversation-metric">
                    立場偏移量：{conversation.stance_shift_magnitude.toFixed(2)}
                  </span>
                )}
              </div>
              {conversation.summary_text && (
                <p className="kb-conversation-summary">{conversation.summary_text}</p>
              )}
            </div>

            <div className="kb-conversation-message-list">
              {conversation.messages.length === 0 && (
                <div className="kb-empty-hint">這筆對話沒有訊息內容。</div>
              )}
              {conversation.messages.map((message) => (
                <article className={messageRowClass(message.side)} key={message.id}>
                  <span>{message.sender_label}</span>
                  <p>{message.content}</p>
                  <small>{formatTime(message.created_at)}</small>
                </article>
              ))}
            </div>
          </section>

          <section className="kb-conversation-tree-panel">
            <ConversationTreePanel
              key={viewpointId}
              topicTitle={conversation.topic_title}
              treeData={conversation.semantic_tree?.treeData ?? null}
              trees={conversation.semantic_tree?.trees ?? []}
              messageCount={conversation.messages.length}
              mode="matching"
              isActive
              isLoading={false}
              isAnalyzing={false}
              analysisStatus={conversation.semantic_tree?.analysisStatus || 'ready'}
            />

            <div className="kb-section-title">這場對話收錄的觀點</div>
            <div className="kb-highlights-grid">
              {conversation.viewpoints.map((viewpoint) => (
                <div key={viewpoint.id} className="kb-highlight-card">
                  <div className="kb-highlight-meta">
                    <span className="kb-highlight-dimension">{viewpoint.dimension_name}</span>
                    {viewpoint.stance_direction && (
                      <span className="kb-highlight-stance">{formatStance(viewpoint.stance_direction)}</span>
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
            </div>
          </section>
        </div>
      )}
    </div>
  );
};

export default KnowledgeBaseConversationPage;
