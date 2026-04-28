import React from 'react';

function IssueCard({ navigate, issues, issuesLoaded }) {
  const hasIssues = issues.length > 0;

  return (
    <div className="issue-highlight-card">
      <h3 className="card-title">開放中主題</h3>
      
      {/* 議題列表容器：包含捲動功能以應對大量議題數據 */}
      <div className="issue-list scrollable">
        {hasIssues ? (
          // 迭代議題資料並渲染單個議題項目
          issues.map((issue) => (
            <div 
              key={issue.id} 
              className="issue-item"
              // 點擊後根據議題 ID 導向動態路由對話頁面
              onClick={() => navigate(`/topic/${issue.id}`)}
            >
              <span>{issue.title}</span>
              {/* 顯示議題發布日期或更新時間 */}
              <span className="issue-timestamp">{issue.date}</span>
            </div>
          ))
        ) : (
          // 資料加載中或無資料時的預留顯示狀態
          <div className="issue-item" style={{ color: '#999', justifyContent: 'center' }}>
            {issuesLoaded ? '目前沒有可用議題' : '正在整理議題中...'}
          </div>
        )}
      </div>
    </div>
  );
}

export default IssueCard;
