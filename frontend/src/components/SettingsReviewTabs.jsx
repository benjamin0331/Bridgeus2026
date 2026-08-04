import { useNavigate } from 'react-router-dom';
import './SettingsReviewTabs.css';

// 設定頁與觀點知識庫審核頁共用的頂部切換列。兩個頁面本來各有一個側邊欄
// icon，合併成同一個「設定」入口後，用這組 tab 在頁面內部切換，藏側邊欄
// 只留一個 icon。

const TABS = [
  { id: 'settings', label: '設定', path: '/settings' },
  { id: 'review', label: '審核', path: '/viewpoint-review' },
];

function SettingsReviewTabs({ active }) {
  const navigate = useNavigate();

  return (
    <div className="srt-tab-row" role="tablist" aria-label="研究者頁面切換">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={active === tab.id}
          className={`srt-tab-btn${active === tab.id ? ' is-active' : ''}`}
          onClick={() => navigate(tab.path)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export default SettingsReviewTabs;
