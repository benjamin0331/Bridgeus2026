import React, { useState } from 'react';
import './KnowledgeBase.css';

const KnowledgeBase = () => {
  // 推薦搜尋議題列表
  const [recommendations] = useState([
    "死刑存廢與人權保障", "核能發電在減碳中的角色", 
    "居住正義與囤房稅 2.0", "人工智慧對勞動力市場的衝擊", 
    "代理孕母合法化的倫理辯論", "數位中介法與言論自由"
  ]);

  // 使用者最近搜尋歷史記錄
  const [searchHistory] = useState([
    "2026 健保費率調整方案", "性別平權運動的歷史演進", 
    "城鄉教育資源差距數據", "再生能源轉型成本評估"
  ]);

  // 熱門搜尋議題及其點擊次數 (Hits)
  const [trending] = useState([
    { name: "基本工資調漲對物價影響", hits: "2.4k/Hits" },
    { name: "最低投票年齡下修至 18 歲", hits: "1.8k/Hits" },
    { name: "跨國同性婚姻配套法律", hits: "1.2k/Hits" },
    { name: "心理健康假納入勞基法", hits: "856/Hits" }
  ]);

  return (
    <div className="kb-container">
      {/* 左側：搜尋功能與歷史記錄區塊 */}
      <div className="kb-left-section">
        {/* 搜尋欄位組件 */}
        <div className="kb-search-bar">
          <img src="/search.png" alt="Search" className="search-icon-img" />
          <input type="text" placeholder="搜尋議題..." />
        </div>

        {/* 推薦議題標籤雲 */}
        <div className="kb-section-title">猜你想搜</div>
        <div className="kb-tag-cloud">
          {recommendations.map((tag, index) => (
            <div key={index} className="kb-tag">{tag}</div>
          ))}
        </div>

        <hr className="kb-divider" />

        {/* 最近搜尋紀錄清單 */}
        <div className="kb-section-title">最近搜索記錄</div>
        <div className="kb-history-list">
          {searchHistory.map((item, index) => (
            <div key={index} className="kb-history-item">{item}</div>
          ))}
        </div>
      </div>

      {/* 右側：熱門趨勢與數據統計區塊 */}
      <div className="kb-right-section">
        <div className="kb-blue-card">
          <h3>近期熱門</h3>
          <div className="kb-trending-list">
            {trending.map((item, index) => (
              <div key={index} className="kb-trending-item">
                <span>{item.name}</span>
                <span>{item.hits}</span>
              </div>
            ))}
          </div>
          
          {/* 數據圖表預留位置 (圓餅圖樣式) */}
          <div className="kb-chart-placeholder">
            <div className="dummy-pie-chart"></div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default KnowledgeBase;