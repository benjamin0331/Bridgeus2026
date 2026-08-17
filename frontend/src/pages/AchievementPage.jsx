import { useEffect, useState } from 'react';
import './AchievementPage.css';
import { fetchAchievements } from '../api/achievements';
import LevelSummaryCard from './LevelSummaryCard';

function AchievementPage() {
  const [categories, setCategories] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchAchievements()
      .then((data) => {
        if (cancelled) return;
        // 這裡刻意丟棄回應裡的 newly_unlocked：解鎖通知一律由 App 在登入時統一
        // 跳，成就頁只負責顯示。要把它接回 toast 得讓這頁拿到 App 的 state，
        // 為一個邊緣情境（打開成就頁本身觸發了解鎖）增加耦合不划算。
        setCategories(data.categories ?? []);
      })
      .catch((err) => {
        if (cancelled) return;
        // 不要靜靜消失：畫面給訊息、console 留線索（同 LevelSummaryCard 的作法）。
        console.warn('成就資料讀取失敗：', err?.message ?? err);
        setError('成就資料讀取失敗，請重新整理再試一次。');
      })
      .finally(() => {
        // 元件已卸載就不要再 setState（cancelled 時連 loading 都不用關）。
        if (cancelled) return;
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="achievement-page">
      <div className="achievement-heading">
        <span className="achievement-kicker">Achievement</span>
        <h1>成就</h1>
        <p>在 TakeABridge 的每一步，都會留下屬於你的足跡與頭銜。</p>
      </div>

      <LevelSummaryCard />

      {loading && <p className="achievement-loading">正在讀取成就...</p>}
      {!loading && error && <p className="achievement-error">{error}</p>}

      <div className="achievement-body">
        {!loading && categories.map((category) => (
          <section className="achievement-category" key={category.id}>
            <h2 className="achievement-category-title">{category.title}</h2>
            <div className="achievement-grid">
              {category.items.map((item) => {
                const locked = !item.unlocked;
                return (
                  <article
                    className={`achievement-card${locked ? ' achievement-card--locked' : ''}`}
                    key={item.code}
                  >
                    <div className="achievement-row-main">
                      <span className="achievement-name">{item.name}</span>
                      {locked
                        ? <span className="achievement-locked-tag">· 未獲得</span>
                        : item.title && <span className="achievement-title-badge">{item.title}</span>}
                    </div>
                    {!locked && <p className="achievement-desc">{item.description}</p>}
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
