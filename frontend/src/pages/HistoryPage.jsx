import { useEffect, useRef, useState } from 'react';
import api from '../api/client';
import ConversationTreePanel from '../components/ConversationTreePanel';
import './HistoryPage.css';

const FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'ai', label: 'AI 對話' },
  { id: 'match', label: '真人對話' },
];

// 給 ConversationTreePanel 的 trees prop 用的穩定空陣列參照。內文用
// `... || []` 這種寫法每次 render 都會生一個「內容一樣但參照不同」的新
// 陣列，會讓 ConversationTreePanel 內部依賴 trees 的 useMemo 判斷成「變了」
// 而重新計算，一路連動到 D3 的重繪 effect，導致每次 render（包含拖動時間
// 軸滑桿的每一格）都整個重畫一次樹狀圖，非常卡。用同一個陣列參照就不會
// 誤觸發。
const EMPTY_TREES = [];

function formatHistoryTime(value) {
  if (!value) {
    return '時間未知';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return new Intl.DateTimeFormat('zh-TW', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function conversationTypeLabel(kind) {
  return kind === 'match' ? '真人對話' : 'AI 對話';
}

function messageRowClass(role) {
  return role === 'user' ? 'history-message-row is-user' : 'history-message-row';
}

function hasAnalyzedTree(treePayload) {
  return Array.isArray(treePayload?.analyzedSourceIds) && treePayload.analyzedSourceIds.length > 0;
}

function truncateMessagePreview(text, maxLength = 24) {
  const cleaned = String(text || '').replace(/\s+/g, ' ').trim();
  if (!cleaned) {
    return '（空白訊息）';
  }
  return cleaned.length <= maxLength ? cleaned : `${cleaned.slice(0, maxLength)}…`;
}

// AI 對話跟真人配對房間都支援 CCND 時間軸，統一打
// history/conversations/{kind}/{id}/semantic-tree/timeline/ 這支 endpoint。
//
// 每個人的想法脈絡圖只會收錄「自己」傳的訊息（後端分析時就是這樣分開整理
// 的，AI 對話那邊也一樣只分析 user_prompt，不分析 AI 的回覆），對方／AI
// 的回覆在這棵樹裡永遠不會有對應的節點，時間軸打 API 只會拿到 100% 必定
// 發生的 404。與其讓使用者拖到那些點才被告知「這則沒有」，乾脆直接把它們
// 從時間軸的可選位置裡濾掉，時間軸只在自己傳的訊息之間跳。
function timelineMessagesFor(detail) {
  if (!detail || !Array.isArray(detail.messages)) {
    return [];
  }
  return detail.messages.filter((message) => message.role === 'user');
}

const PHASE_LABELS = ['對話前段', '對話中段', '對話後段'];

function phaseLabel(index, total) {
  if (total === 3) {
    return PHASE_LABELS[index];
  }
  return `第 ${index + 1} 段`;
}

/** 概念展開回顧。兩層：白話一句話 + 可展開的「各階段新增」。
 *  全部用一般人看得懂的說法，不出現 Jaccard、T1/T2 這種研究術語。
 *  資料只含使用者自己那一側（後端已剝掉對方的 partner_side）。 */
function CcndInsightsPanel({ insights, showRaw, onToggleRaw }) {
  const { summary, novelty } = insights;
  const firstAppearance = novelty?.first_appearance || [];
  const newMicros = summary?.new_micros_per_segment || [];
  // 第一段之後才首次出現的概念數 = 對話展開過程中「長出來」的部分
  const laterNew = newMicros.slice(1).reduce((total, count) => total + count, 0);

  return (
    <div className="history-insights">
      <div className="history-insights-head">
        <strong>你的概念展開回顧</strong>
        <button type="button" className="history-insights-toggle" onClick={onToggleRaw}>
          {showRaw ? '收起各階段' : '看各階段'}
        </button>
      </div>

      <p className="history-insights-plain">
        {`這場對話你談到 ${summary.final_macro_count}／${summary.macro_denominator} 個面向、`}
        {`${summary.final_micro_count} 個具體概念。`}
        {laterNew > 0
          ? `其中 ${laterNew} 個是對話開始之後才第一次出現的，代表你的討論範圍隨對話逐步展開。`
          : '你的概念大多在對話前段就已經提出。'}
      </p>

      {firstAppearance.length > 0 && (
        <ol className="history-insights-timeline">
          {firstAppearance.map((entry) => (
            <li key={entry.key}>
              <span className="history-insights-node">{entry.node_name}</span>
              <span className="history-insights-meta">
                {`${entry.anchor_name} · 第 ${entry.first_hit_ordinal} 則`}
              </span>
            </li>
          ))}
        </ol>
      )}

      {showRaw && newMicros.length > 0 && (
        <div className="history-insights-raw">
          <span className="history-insights-raw-title">把對話平均分成幾段，各段新出現的概念數：</span>
          {newMicros.map((count, index) => (
            <div key={phaseLabel(index, newMicros.length)}>
              {`${phaseLabel(index, newMicros.length)}：新增 ${count} 個概念`}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function HistoryPage() {
  const [filter, setFilter] = useState('all');
  const [items, setItems] = useState([]);
  const [selectedItem, setSelectedItem] = useState(null);
  const [detail, setDetail] = useState(null);
  const [isListLoading, setIsListLoading] = useState(false);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const [timelineIndex, setTimelineIndex] = useState(null); // 放開滑桿後「已提交」的位置；null = 即時狀態
  // 拖動中滑桿本身停留的位置，只影響滑桿把手跟文字提示要不要跟著手指走；
  // 在放開之前，畫面上顯示的樹狀圖完全不受這個值影響，才不會拖到一半就
  // 突然閃成空的（timelineIndex 還沒變，displayedTreeData 也還沒變）。
  const [previewIndex, setPreviewIndex] = useState(null);
  const [timelineTreeData, setTimelineTreeData] = useState(null);
  // 這則訊息「新生」的節點（後端 bornNodes，依 sourceMessageId 判定）。
  const [bornNodes, setBornNodes] = useState([]);
  // 概念展開回顧：問卷（含 Part F）完成後才拿得到，只含自己那側。
  const [insights, setInsights] = useState(null);
  const [showRawMetrics, setShowRawMetrics] = useState(false);
  const [isTimelineLoading, setIsTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState('');
  const [analysisError, setAnalysisError] = useState('');
  // 每次切換對話或重新拖動時間軸都會+1，讓晚到的舊請求發現自己已經過期，
  // 不會在使用者已經切到別筆對話之後才把過期的樹狀態蓋上去。
  const timelineRequestIdRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    const loadHistory = async () => {
      setIsListLoading(true);
      setError('');
      setAnalysisError('');
      try {
        const response = await api.get(`/api/history/conversations/?type=${filter}`);
        if (cancelled) return;

        const nextItems = Array.isArray(response.data?.results) ? response.data.results : [];
        setItems(nextItems);
        if (!nextItems.length) {
          setDetail(null);
        }
        setSelectedItem((current) => {
          if (current && nextItems.some((item) => item.kind === current.kind && item.id === current.id)) {
            return current;
          }
          return nextItems[0] || null;
        });
      } catch (requestError) {
        if (cancelled) return;
        setItems([]);
        setSelectedItem(null);
        setDetail(null);
        setError(requestError?.response?.data?.detail || '目前無法讀取歷史對話。');
      } finally {
        if (!cancelled) {
          setIsListLoading(false);
        }
      }
    };

    void loadHistory();

    return () => {
      cancelled = true;
    };
  }, [filter]);

  useEffect(() => {
    if (!selectedItem) {
      return undefined;
    }

    let cancelled = false;

    const loadDetail = async () => {
      setIsDetailLoading(true);
      setAnalysisError('');
      setTimelineIndex(null);
      setTimelineTreeData(null);
      setBornNodes([]);
      setInsights(null);
      setShowRawMetrics(false);
      setTimelineError('');
      timelineRequestIdRef.current += 1;
      try {
        const response = await api.get(`/api/history/conversations/${selectedItem.kind}/${selectedItem.id}/`);
        if (!cancelled) {
          setDetail(response.data);
        }
      } catch (requestError) {
        if (!cancelled) {
          setDetail(null);
          setAnalysisError(requestError?.response?.data?.detail || '目前無法讀取這筆對話。');
        }
      } finally {
        if (!cancelled) {
          setIsDetailLoading(false);
        }
      }
    };

    void loadDetail();

    return () => {
      cancelled = true;
    };
  }, [selectedItem]);

  // 概念展開回顧只在問卷（含 Part F）完成後才拿得到——後端同樣會擋，這裡是不去要。
  useEffect(() => {
    // 鎖住時不去要（切換對話時 loadDetail 已清掉舊的 insights）。
    if (!detail?.timeline_unlocked) {
      return undefined;
    }

    let cancelled = false;
    const loadInsights = async () => {
      try {
        const response = await api.get(
          `/api/history/conversations/${detail.kind}/${detail.id}/ccnd-insights/`,
        );
        if (!cancelled) {
          setInsights(response.data);
        }
      } catch {
        if (!cancelled) {
          setInsights(null);
        }
      }
    };

    void loadInsights();

    return () => {
      cancelled = true;
    };
  }, [detail]);

  const handleAnalyzeTree = async () => {
    if (!detail || isAnalyzing) {
      return;
    }

    setIsAnalyzing(true);
    setAnalysisError('');
    try {
      const response = await api.post(
        `/api/history/conversations/${detail.kind}/${detail.id}/semantic-tree/analyze/`,
      );
      setDetail(response.data);
    } catch (requestError) {
      setAnalysisError(
        requestError?.response?.data?.message ||
          requestError?.response?.data?.detail ||
          '目前無法整理想法脈絡圖。',
      );
    } finally {
      setIsAnalyzing(false);
    }
  };

  const treePayload = detail?.semantic_tree;
  const shouldShowAnalyzeButton = detail && !hasAnalyzedTree(treePayload);
  const timelineMessages = timelineMessagesFor(detail);
  // 樹狀圖只看「已提交」的位置，不受拖動中的 previewIndex 影響。
  const isViewingLatest = timelineIndex === null || timelineIndex === timelineMessages.length - 1;
  // 看即時狀態時直接讀最新的 treePayload（例如剛按完「整理想法脈絡圖」會馬上反映）；
  // 停在某個歷史時間點時，才顯示上次成功抓回來的歷史快照（timelineTreeData）。
  const displayedTreeData = isViewingLatest ? treePayload?.treeData || null : timelineTreeData;

  // 文字提示跟滑桿把手位置：拖動中優先顯示手指目前停的位置（previewIndex），
  // 放開之後才會跟已提交的 timelineIndex 一致。
  const sliderPosition = previewIndex !== null
    ? previewIndex
    : (timelineIndex === null ? timelineMessages.length - 1 : timelineIndex);
  const isLabelAtLatest = sliderPosition === timelineMessages.length - 1;
  const labelMessage = !isLabelAtLatest ? timelineMessages[sliderPosition] : null;

  // 拖動中只更新滑桿位置本身（給文字提示跟把手用），不打 API 也不改變畫面
  // 上顯示的樹狀圖——樹狀圖只看「已提交」的 timelineIndex，才不會拖到一半
  // 就先閃成空的。放開滑桿（或用鍵盤操作放開）時才真的送出這次的位置。
  const handleTimelineDrag = (event) => {
    setPreviewIndex(Number(event.target.value));
  };

  const handleTimelineCommit = async (event) => {
    const index = Number(event.target.value);
    setPreviewIndex(null);
    if (!detail || !timelineMessages.length) {
      return;
    }

    // 在切換到新位置之前，先把「這一刻畫面上顯示的東西」存成備援快照——
    // 這裡的 displayedTreeData 是根據上一次「已提交」的位置算出來的，不會
    // 被拖動中的 previewIndex 影響，所以拿到的一定是使用者上一刻實際看到
    // 的畫面。如果 timelineTreeData 一直沒被用到（例如從即時狀態第一次拖
    // 開），它會停在 loadDetail 重置時設的 null，之後拖到抓不到資料的位置
    // 就會顯示空畫面而不是「上一個有結果的狀態」，所以每次提交前都要補進。
    setTimelineTreeData(displayedTreeData);

    setTimelineIndex(index);
    setTimelineError('');
    // 每次放開滑桿都先讓前一個還沒回來的請求失效，不管這次放開的位置
    // 要不要重新打 API——否則舊請求晚一步回來時可能蓋掉這次的畫面。
    const requestId = ++timelineRequestIdRef.current;

    // 拖到最新一格不用另外呼叫 timeline API——displayedTreeData 在 isViewingLatest
    // 為真時本來就是直接讀最新的 treePayload，timelineIndex 一更新畫面就會自動跟上。
    if (index === timelineMessages.length - 1) {
      // 回到即時狀態沒有「這則新增了什麼」可言。
      setBornNodes([]);
      return;
    }

    const targetMessage = timelineMessages[index];
    setIsTimelineLoading(true);
    try {
      const response = await api.get(
        `/api/history/conversations/${detail.kind}/${detail.id}/semantic-tree/timeline/`,
        {
          // message.id 是「match-38」/「ai-7-user」這種前綴過的 React key，
          // semantic tree 記錄的 sourceMessageId 對應的是原始訊息/回合 id，
          // 要用 source_id 才會查得到。
          params: { as_of_message_id: targetMessage.source_id },
        },
      );
      // 這段等待期間使用者可能已經切換到別筆對話或拖到別的時間點，
      // 這種情況下這個回應已經過期，不能再套用到畫面上。
      if (requestId !== timelineRequestIdRef.current) {
        return;
      }
      setTimelineTreeData(response.data?.treeData || null);
      setBornNodes(response.data?.bornNodes || []);
    } catch (requestError) {
      if (requestId !== timelineRequestIdRef.current) {
        return;
      }
      setBornNodes([]);
      // 保留畫面上一個成功顯示的樹狀圖，不要清空，只提示這個時間點暫時看不到。
      setTimelineError(
        requestError?.response?.status === 404
          ? '這則訊息還在分析中，暫時看不到當時的想法脈絡圖，先顯示上一個有結果的狀態。'
          : requestError?.response?.data?.detail || '目前無法讀取這個時間點的想法脈絡圖。',
      );
    } finally {
      if (requestId === timelineRequestIdRef.current) {
        setIsTimelineLoading(false);
      }
    }
  };

  return (
    <div className="history-page">
      <section className="history-list-panel">
        <div className="history-page-heading">
          <span className="history-page-kicker">History</span>
          <h1>歷史對話</h1>
          <p>回顧 AI 與真人配對紀錄，查看完整訊息與我的想法脈絡圖。</p>
        </div>

        <div className="history-filter-row" role="tablist" aria-label="歷史對話類型">
          {FILTERS.map((item) => (
            <button
              key={item.id}
              className={`history-filter-btn${filter === item.id ? ' is-active' : ''}`}
              type="button"
              onClick={() => setFilter(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="history-record-list">
          {isListLoading && <div className="history-empty-card">正在讀取歷史紀錄...</div>}
          {!isListLoading && error && <div className="history-empty-card error">{error}</div>}
          {!isListLoading && !error && items.length === 0 && (
            <div className="history-empty-card">目前還沒有可顯示的歷史對話。</div>
          )}
          {!isListLoading && items.map((item) => (
            <button
              key={`${item.kind}-${item.id}`}
              className={`history-record-card${selectedItem?.kind === item.kind && selectedItem?.id === item.id ? ' is-active' : ''}`}
              type="button"
              onClick={() => setSelectedItem(item)}
            >
              <span className={`history-kind-badge kind-${item.kind}`}>
                {conversationTypeLabel(item.kind)}
              </span>
              <strong>{item.topic_title || `議題 ${item.topic_id}`}</strong>
              <span className="history-preview">
                {item.last_message_preview || '尚無內容摘要'}
              </span>
              <span className="history-card-meta">
                {item.message_count} 則訊息 · {formatHistoryTime(item.last_activity_at)}
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="history-message-panel">
        <div className="history-panel-header">
          <span className="history-page-kicker">
            {detail ? conversationTypeLabel(detail.kind) : 'Conversation'}
          </span>
          <h2>{detail?.topic_title || '選擇一筆對話'}</h2>
          {detail && (
            <p>{detail.message_count} 則訊息 · {formatHistoryTime(detail.last_activity_at)}</p>
          )}
        </div>

        <div className="history-message-list">
          {isDetailLoading && <div className="history-empty-card">正在讀取對話內容...</div>}
          {!isDetailLoading && analysisError && !detail && (
            <div className="history-empty-card error">{analysisError}</div>
          )}
          {!isDetailLoading && !detail && !analysisError && (
            <div className="history-empty-card">請從左側選擇一筆歷史對話。</div>
          )}
          {!isDetailLoading && detail?.messages?.length === 0 && (
            <div className="history-empty-card">這筆對話沒有訊息內容。</div>
          )}
          {!isDetailLoading && detail?.messages?.map((message) => (
            <article className={messageRowClass(message.role)} key={message.id}>
              <span>{message.sender_label}</span>
              <p>{message.content}</p>
              <small>{formatHistoryTime(message.created_at)}</small>
            </article>
          ))}
        </div>
      </section>

      <section className="history-tree-panel">
        <div className="history-tree-action">
          {shouldShowAnalyzeButton && (
            <button
              className="history-analyze-btn"
              type="button"
              onClick={handleAnalyzeTree}
              disabled={isAnalyzing}
            >
              {isAnalyzing ? '整理中...' : '整理想法脈絡圖'}
            </button>
          )}
          {analysisError && detail && <span className="history-analysis-error">{analysisError}</span>}
        </div>
        <ConversationTreePanel
          topicTitle={detail?.topic_title || '想法脈絡圖'}
          treeData={displayedTreeData}
          trees={isViewingLatest ? treePayload?.trees || EMPTY_TREES : EMPTY_TREES}
          messageCount={detail?.messages?.length || 0}
          mode={detail?.kind === 'match' ? 'matching' : 'ai'}
          isActive={Boolean(detail)}
          isLoading={isDetailLoading}
          isAnalyzing={isAnalyzing}
          analysisStatus={treePayload?.analysisStatus || 'ready'}
        />
        {/* 時間軸在對話後問卷（含 Part F）完成前是鎖住的——受試者若能先回顧自己的
            CCND，就會在回答 C3／F4 這些 CCND 自陳題前看到被測量的東西。後端也會擋，
            這裡只是不顯示入口。 */}
        {detail && !detail.timeline_unlocked && hasAnalyzedTree(treePayload) && (
          <div className="history-timeline-bar history-timeline-locked">
            <span className="history-timeline-label">
              完成對話後問卷（含平台體驗回饋）後，就能在這裡逐則回顧想法脈絡圖的變化。
            </span>
          </div>
        )}
        {detail?.timeline_unlocked && hasAnalyzedTree(treePayload) && timelineMessages.length > 1 && (
          <div className="history-timeline-bar">
            <div className="history-timeline-row">
              <span className="history-timeline-label">
                {isLabelAtLatest ? (
                  '目前狀態（即時）'
                ) : (
                  <>
                    {/* timelineMessages 只會有自己傳的訊息，不用再標「我：」 */}
                    {`我的第 ${sliderPosition + 1}／${timelineMessages.length} 則訊息：`}
                    <strong>{`「${truncateMessagePreview(labelMessage?.content)}」`}</strong>
                    {` · ${formatHistoryTime(labelMessage?.created_at)}`}
                  </>
                )}
                {isTimelineLoading && '（讀取中…）'}
              </span>
            </div>
            <input
              className="history-timeline-slider"
              type="range"
              min={0}
              max={timelineMessages.length - 1}
              value={sliderPosition}
              onChange={handleTimelineDrag}
              onMouseUp={handleTimelineCommit}
              onTouchEnd={handleTimelineCommit}
              onKeyUp={handleTimelineCommit}
              disabled={isTimelineLoading}
            />
            {!isLabelAtLatest && !isTimelineLoading && (
              <span className="history-timeline-born">
                {bornNodes.length
                  ? `這則新增了 ${bornNodes.length} 個節點：${bornNodes.map((node) => node.name).join('、')}`
                  : '這則沒有新增節點（併入既有節點或沒有新的主張）。'}
              </span>
            )}
            {timelineError && <span className="history-timeline-error">{timelineError}</span>}
          </div>
        )}
        {detail?.timeline_unlocked && insights && <CcndInsightsPanel
          insights={insights}
          showRaw={showRawMetrics}
          onToggleRaw={() => setShowRawMetrics((shown) => !shown)}
        />}
      </section>
    </div>
  );
}

export default HistoryPage;
