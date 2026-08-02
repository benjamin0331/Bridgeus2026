import './AchievementPage.css';
import { CATEGORIES } from './achievements.data';

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
