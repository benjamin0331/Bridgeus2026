import { useCallback, useEffect, useState } from 'react';
import ActionCard from './ActionCard';
import IssueCard from './IssueCard';
import ConversationTreePanel from './ConversationTreePanel';
import './OnboardingTour.css';

// 這三份是「示意圖要長得跟真的一樣」的關鍵：示意圖不重畫，直接套真實頁面的 class。
// 全站 CSS 都是全域的（沒有 CSS Modules），正式建置本來就會打包成同一支，所以在這裡
// 早一步 import 不會改變任何既有頁面的樣式。
import '../pages/HomePage.css';
import '../pages/KnowledgeBase.css';
import '../pages/TopicChat.css';

// 首次登入的新手指引。整段是「假介面」——不會真的進聊天室、不打任何 API，
// 所以受試者在還沒填立場問卷之前就能安全地看完。
//
// 目前是「每次進入都會出現」。等後端加上「這個帳號已完成導覽」的欄位之後，
// 把 shouldOpenOnMount() 換成讀那個欄位、並在 close() 裡打 API 標記完成即可，
// 其餘邏輯都不用動。
function shouldOpenOnMount() {
  return true;
}

/* ── 各張投影片的示意圖：全部沿用真實頁面的元件與 class ─────────────────── */

// 示意圖裡的議題資料，只給 IssueCard 當假資料用，不會導向任何路徑
const MOCK_ISSUES = [
  { id: 102, title: '台灣核能議題討論', date: '08/29' },
  { id: 103, title: '女性義務兵役討論', date: '08/29' },
];

const noop = () => {};

// 主畫面全景：左邊「開放中主題」＋右邊兩個入口卡，三塊一起看
function MockHomeOverview() {
  return (
    <div className="ob-frame ob-frame-overview">
      <div className="dashboard-grid">
        <IssueCard navigate={noop} issues={MOCK_ISSUES} issuesLoaded entryMode="split" />
        <aside className="action-button-stack">
          <ActionCard text="觀點知識庫" className="knowledge-base-entry" />
          <ActionCard text="虛擬大廳" className="virtual-chat-entry" />
        </aside>
      </div>
    </div>
  );
}

// 左邊區塊單獨放大：同一個議題會有「AI」與「配對」兩張入口
function MockIssueCard() {
  return (
    <div className="ob-frame ob-frame-issues">
      <IssueCard navigate={noop} issues={MOCK_ISSUES} issuesLoaded entryMode="split" />
    </div>
  );
}

function MockKnowledgeBase() {
  return (
    <div className="ob-frame ob-frame-kb">
      <div className="kb-section-title">選擇議題</div>
      <div className="kb-tag-cloud">
        <span className="kb-tag kb-tag-active">台灣核能議題討論</span>
        <span className="kb-tag">女性義務兵役討論</span>
      </div>

      <div className="kb-section-title">「台灣核能議題討論」熱門對話 Top 5</div>
      <div className="kb-highlights-grid">
        <div className="kb-highlight-group-card">
          <div className="kb-highlight-group-item">
            <div className="kb-highlight-meta">
              <span className="kb-highlight-side">A方</span>
              <span className="kb-highlight-dimension">能源問題</span>
              <span className="kb-highlight-stance">中立</span>
            </div>
            <p className="kb-highlight-summary">SMR 相關新技術</p>
            <div className="kb-highlight-quote">
              <div className="kb-highlight-quote-label">使用者發言</div>
              <p className="kb-highlight-quote-text">
                福島事故確實是重要教訓，但新一代反應爐設計在安全機制上已大幅提升
              </p>
            </div>
            <div className="kb-highlight-quote">
              <div className="kb-highlight-quote-label">對方回應</div>
              <p className="kb-highlight-quote-text">
                技術進步是事實，但台灣位在地震帶上，無法靠反應爐設計解決
              </p>
            </div>
            <div className="kb-highlight-footer">
              <span>被引用 1 次</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MockLobby() {
  return (
    <div className="ob-frame ob-frame-lobby">
      <img src="/frogs/lobby_scene.png" alt="虛擬大廳場景" className="ob-lobby-shot" />
    </div>
  );
}

function MockChat() {
  return (
    <div className="ob-frame ob-frame-chat">
      <div className="topic-title-row">
        <h1 className="topic-title">台灣核能議題討論</h1>
        <span className="topic-mode-badge mode-ai">AI 模式</span>

        <div className="metric-row metric-row-header" aria-label="對話即時指標">
          <div className="metric-card">
            <span className="metric-label metric-label-with-info">
              <span className="metric-label-text">我的論述移動</span>
            </span>
            <strong className="metric-value metric-value-equation">0.0000 +0.5321</strong>
          </div>
          <div className="metric-card">
            <span className="metric-label">我的立場分數</span>
            <strong className="metric-value">5.12</strong>
          </div>
          <div className="metric-card">
            <span className="metric-label">對話訊息數</span>
            <strong className="metric-value">14</strong>
          </div>
        </div>
      </div>

      <div className="chat-messages-display ob-static-scroll">
        <div className="message-row">
          <div className="message-user-info">
            <img src="/logo.png" alt="" className="message-avatar" />
            <span className="message-username">BridgeUs</span>
          </div>
          <div className="message-bubble">
            核廢料的選址問題確實是台灣核電爭議的核心卡點，現有核電廠的用過燃料池已經快要滿了
          </div>
        </div>
        <div className="message-row user-message">
          <div className="message-user-info">
            <img src="/icon.jpg" alt="" className="message-avatar" />
            <span className="message-username">你</span>
          </div>
          <div className="message-bubble">核能廠爆炸的話要幾年才能復原啊</div>
        </div>
      </div>
    </div>
  );
}

// 語意樹的示意圖直接掛真正的 ConversationTreePanel，餵它一份假的樹資料。
// 這個元件是純呈現的（不打 API、不連 WebSocket），所以畫出來的東西跟對話室裡
// 看到的完全是同一份程式碼，不是另外畫一張像的圖。
function mockTree(withStance) {
  const point = (id, name, stance) => ({
    id,
    name,
    children: [],
    messages: [{ text: `${name}相關的發言`, stance: withStance ? stance : '中立' }],
  });
  return {
    id: 'root',
    name: '台灣核能議題討論',
    children: [
      { id: 'a1', name: '核安風險', children: [point('p1', '斷層帶疑慮', '反對'), point('p2', '耐震補強', '支持')] },
      { id: 'a2', name: '發電成本', children: [point('p3', '延役費用', '支持')] },
      { id: 'a3', name: '核廢處置', children: [point('p4', '最終處置場', '反對')] },
      { id: 'a4', name: '供電穩定', children: [point('p5', '備轉容量', '支持')] },
      { id: 'a5', name: '減碳效益', hiddenUntilUsed: true, children: [] },
      { id: 'a6', name: '公民信任', hiddenUntilUsed: true, children: [] },
    ],
  };
}

function MockCcndStance() {
  return <MockCcnd withStance />;
}

function MockCcnd({ withStance = false }) {
  return (
    <div className="ob-frame ob-frame-ccnd">
      <div className="right-major-feature-box">
        <ConversationTreePanel
          topicTitle="台灣核能議題討論"
          treeData={mockTree(withStance)}
          messageCount={14}
          mode="ai"
          isActive
          analysisStatus="ready"
        />
      </div>
    </div>
  );
}

/* ── 投影片內容 ───────────────────────────────────────────────────────── */

/* ════════════════════════════════════════════════════════════════════════
   文案集中區：要改字只改這裡，底下的程式碼一行都不用動。

   注意：
   - welcome.title 裡的 {name} 會被替換成使用者名稱，要保留這四個字元
   - slides 的順序就是投影片順序，lines 想寫幾行就幾行（不限三行）
   - 每張投影片配哪張示意圖由下面的 SLIDE_MOCKS 決定，跟這裡一一對應
   ════════════════════════════════════════════════════════════════════════ */
const COPY = {
  // 歡迎頁維持定稿，不是佔位符
  welcome: {
    kicker: 'TakeAbridge',
    title: '歡迎，{name}！',       // {name} 會換成使用者名稱
    body1: '這是一個讓立場不同的人好好把話講完的地方',
    body2: '要先花一分鐘看看各個地方在做什麼嗎？',
    skip: '跳過',
    start: '開始新手指引',
  },

  // 三顆導覽按鈕維持定稿，不是佔位符
  buttons: {
    back: '上一步',
    next: '下一步',
    done: '開始使用',
  },

  // 最後一頁「開始使用」旁的小提示（設定頁的重看功能還沒做，這裡先只放文字）
  replayHint: '想再看一次？之後可以在設定中重新開啟新手指引',

  slides: [
    {
      title: '主畫面',
      lines: [
        '「開放中主題」為正式對話的入口，可從這裡挑選議題、討論對象',
        '「觀點知識庫」中將他人的對話取其精華存下，收錄已通過審核的片段',
        '「虛擬大廳」是一個可以自由走動的虛擬場景，用更加輕鬆的方式跟他人討論公共議題',
      ],
    },
    {
      title: '主畫面 · 開放中主題',
      lines: [
        '「AI」和 AI 對談，隨時都能開始，AI 會站在你的對立面',
        '「配對」與真人匹配，系統會幫你找立場相反的另一位參與者',
        '兩者在開始對話之前都要先填立場問卷，用來決定立場分數與配對對象',
      ],
    },
    {
      title: '觀點知識庫',
      lines: [
        '選擇議題:可以看到所有網站有在討論的議題',
        '觀點展示:可以看到所選議題TOP5的熱門點擊的觀點之聊天紀錄，及這則發言所在的觀點分類還有立場',
      ],
    },
    {
      title: '虛擬大廳',
      lines: [
        '進去後為圖中的 2D 場景，可以化身青蛙自在漫游、遇到其他參與者',
        '可發佈自己感興趣的議題，同時也能瀏覽其他參與者發佈的内容',
      ],
    },
    {
      title: '對話室 · 上方數值',
      lines: [
        '我的論述移動：你這句話跟最初的立場陳述差多遠',
        '我的立場分數：根據前測問卷算出來的立場數字',
        '對話訊息數：這場對話中的累積發言數',
      ],
    },
    {
      title: '對話室 · 想法脈絡',
      lines: [
        '中心為討論主題，往外第一圈是六個大分類，最外圈為對話中討論到的内容',
        '初始所有分類默認為灰的，對話中討論到哪一類，那一類才會亮起來並長出節點',
      ],
    },
    {
      title: '對話室 · 想法脈絡',
      lines: [
        '綠色是支持、紅色是反對，中立或暫時判斷不出立場的維持原色',
        '同一個節點被新的發言重新判定時，顏色會跟著更新',
        '點擊任意一個節點都可以看到它對應的原始發言與整理理由',
      ],
    },
  ],
};

// 每張投影片配的示意圖，順序對應 COPY.slides
const SLIDE_MOCKS = [
  MockHomeOverview,
  MockIssueCard,
  MockKnowledgeBase,
  MockLobby,
  MockChat,
  MockCcnd,
  MockCcndStance,
];

const SLIDES = COPY.slides.map((copy, i) => ({ ...copy, Mock: SLIDE_MOCKS[i] }));
/* ── 主元件 ───────────────────────────────────────────────────────────── */

function OnboardingTour({ userName }) {
  // -1 = 歡迎頁，0..n-1 = 投影片，null = 不顯示
  const [step, setStep] = useState(() => (shouldOpenOnMount() ? -1 : null));

  const close = useCallback(() => {
    // 後端加上「已完成導覽」欄位之後，這裡要多打一支 API 把它標記起來
    setStep(null);
  }, []);

  const next = useCallback(() => {
    setStep((s) => {
      if (s === null) return null;
      return s + 1 >= SLIDES.length ? null : s + 1;
    });
  }, []);

  const back = useCallback(() => {
    setStep((s) => (s === null || s <= 0 ? s : s - 1));
  }, []);

  // 滑鼠點點點是主要操作，鍵盤只是不想讓人卡住
  useEffect(() => {
    if (step === null) return undefined;
    const onKey = (event) => {
      if (event.key === 'Escape') close();
      else if (event.key === 'ArrowLeft') back();
      else if (step >= 0 && (event.key === 'ArrowRight' || event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        next();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step, next, back, close]);

  if (step === null) return null;

  if (step === -1) {
    return (
      <div className="ob-overlay" role="dialog" aria-modal="true" aria-labelledby="ob-welcome-title">
        <div className="ob-welcome">
          <p className="ob-welcome-kicker">{COPY.welcome.kicker}</p>
          <h2 id="ob-welcome-title">
            {COPY.welcome.title.replace('{name}', userName || '')}
          </h2>
          <p className="ob-welcome-body">
            {COPY.welcome.body1}
            <br />
            {COPY.welcome.body2}
          </p>
          <div className="ob-welcome-actions">
            <button type="button" className="ob-btn ob-btn-ghost" onClick={close}>
              {COPY.welcome.skip}
            </button>
            <button type="button" className="ob-btn ob-btn-primary" onClick={() => setStep(0)}>
              {COPY.welcome.start}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const slide = SLIDES[step];
  const { Mock } = slide;
  const isLast = step === SLIDES.length - 1;

  return (
    // 整片可點：使用者的心智模型是「一直點點點」，所以舞台本身就是下一步按鈕
    <div className="ob-overlay" role="dialog" aria-modal="true" aria-labelledby="ob-slide-title">
      <div className="ob-stage" onClick={next}>
        {/* 捲動放在內層而不是 .ob-stage：捲軸畫在 border-box 上，掛在有圓角的
            外框會露到圓角外面去，內縮一層才關得住，底部按鈕列也不會跟著捲走 */}
        <div className="ob-scroll">
          {/* 示意圖裡有真實元件（語意樹可以縮放拖曳），不能讓它們的點擊冒泡上去翻頁 */}
          <div className="ob-slide-visual" onClick={(event) => event.stopPropagation()}>
            <Mock />
          </div>

          <div className="ob-slide-copy">
            <h2 id="ob-slide-title">{slide.title}</h2>
            {/* key 用索引不用內容：兩行文字可能一模一樣，拿內容當 key 會撞號，
                換頁時舊的 <li> 不會被汰換掉，會一頁一頁疊上去。清單是靜態的、
                換頁時整批換掉，用索引沒有排序錯位的風險。 */}
            <ul className="ob-lines" key={step}>
              {slide.lines.map((line, i) => <li key={i}>{line}</li>)}
            </ul>
          </div>
        </div>

        {/* 按鈕要擋掉冒泡，否則點「上一步」會同時觸發舞台的下一步 */}
        <div className="ob-bar" onClick={(event) => event.stopPropagation()}>
          <button
            type="button"
            className="ob-btn ob-btn-ghost"
            onClick={back}
            disabled={step === 0}
          >
            {COPY.buttons.back}
          </button>

          <div className="ob-dots" aria-hidden="true">
            {SLIDES.map((s, i) => (
              <i key={i} className={`ob-dot-nav ${i === step ? 'is-on' : ''}`} />
            ))}
          </div>

          <div className="ob-bar-end">
            {isLast && <span className="ob-replay-hint">{COPY.replayHint}</span>}
            <button type="button" className="ob-btn ob-btn-primary" onClick={next}>
              {isLast ? COPY.buttons.done : COPY.buttons.next}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default OnboardingTour;
