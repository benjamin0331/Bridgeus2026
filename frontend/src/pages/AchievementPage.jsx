import './AchievementPage.css';

const CATEGORIES = [
  {
    id: 'experience',
    title: '使用體驗',
    items: [
      { name: '初來乍到', how: '首次註冊並登入 TakeABridge', desc: '歡迎來到 TakeABridge，準備好來一場觀點與觀點之間的碰撞了嗎？', title: '築橋新手', unlocked: false },
      { name: '第一聲問候', how: '首次完成真人聊天室對話', desc: '每一段交流，都從友善的一句「你好」開始', title: '上橋新人', unlocked: false },
      { name: 'AI 初體驗', how: '首次與 AI 完成完整對話', desc: '有時候，與 AI 對話也能帶來新的想法！', title: '智橋行者', unlocked: false },
      { name: '初入異次元', how: '首次進入 Godot 世界', desc: '歡迎來到 Godot 世界，瞭解更多的觀點', unlocked: false },
      { name: '求知若渴', how: '首次開啟觀點知識庫', desc: '每一個纍積的觀點，都來自前人的貢獻', unlocked: false },
    ],
  },
  {
    id: 'stance',
    title: '立場變動',
    items: [
      { name: '一念之間', how: '首次完成一場有立場變化的交流', desc: '改變與被説服不是妥協，而是願意重新思考自己的想法', unlocked: true },
      { name: '保持初心', how: '首次完成一場立場維持一致的交流', desc: '經過思考後依然堅持，或許是對自己的觀點足夠堅定', unlocked: true },
      { name: '換個角度', how: '多次完成有立場變化的交流', desc: '一件事總有各種不同的看法，換個角度或許能看見更多', unlocked: true },
      { name: '思辨旅程', how: '多次完成前測與後測', desc: '每一個回答，都在留下自己思考的痕跡', unlocked: true },
    ],
  },
  {
    id: 'engagement',
    title: '對話投入度',
    items: [
      { name: '話匣子', how: '首次完成高輪數對話', desc: '真正的交流，從來不是一句話就結束', unlocked: true },
      { name: '留到最後', how: '多次完成完整聊天流程', desc: '願意陪伴一場場對話走到最後', title: '長橋旅人', unlocked: true },
      { name: '今晚聊個夠', how: '一天完成多場交流', desc: '今晚，渴望瞭解更多觀點的心情根本停不下來', title: '夜橋旅人', unlocked: true },
    ],
  },
  {
    id: 'quality',
    title: '對話品質',
    items: [
      { name: '理性交流', how: '首次完成未偵測到攻擊性內容的交流', desc: '尊重，是展開良好對話的基本要素', unlocked: true },
      { name: '有話好說', how: '累積多場友善交流', desc: '不同立場，也能好好說話', title: '溝通達人', unlocked: true },
    ],
  },
  {
    id: 'longterm',
    title: '長期參與',
    items: [
      { name: '常回來看看', how: '累積使用平台指定天數', desc: '熟悉的身影，再次出現在 TakeABridge', title: '橋上常客', unlocked: true },
      { name: '百戰交流', how: '累積完成指定場數對話', desc: '一句一句，誕生了無數想法', title: '千橋旅人', unlocked: true },
      { name: '一路同行', how: '收藏全部成就内容', desc: '謝謝你，這麽支持我們的畢業專題 ;)', unlocked: true },
    ],
  },
];

function AchievementPage() {
  return (
    <div className="achievement-page">
      <div className="achievement-heading">
        <span className="achievement-kicker">Achievement</span>
        <h1>成就</h1>
        <p>在 TakeABridge 的每一步，都會留下屬於你的足跡與頭銜。</p>
      </div>

      <div className="achievement-body">
        {CATEGORIES.map((category) => (
          <section className="achievement-category" key={category.id}>
            <h2 className="achievement-category-title">{category.title}</h2>
            <div className="achievement-grid">
              {category.items.map((item) => {
                const locked = !item.unlocked;
                return (
                  <article
                    className={`achievement-card${locked ? ' achievement-card--locked' : ''}`}
                    key={item.name}
                  >
                    <div className="achievement-row-main">
                      <span className="achievement-name">{item.name}</span>
                      {locked
                        ? <span className="achievement-locked-tag">· 未獲得</span>
                        : item.title && <span className="achievement-title-badge">{item.title}</span>}
                    </div>
                    {!locked && <p className="achievement-desc">{item.desc}</p>}
                    <span className="achievement-how">{item.how}</span>
                  </article>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

export default AchievementPage;
