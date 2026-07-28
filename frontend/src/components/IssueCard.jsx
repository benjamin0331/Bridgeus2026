import React from 'react';

const ISSUE_ENTRY_MODES = [
  {
    key: 'ai',
    label: 'AI',
    description: '和 AI 對談',
  },
  {
    key: 'match',
    label: '配對',
    description: '等待真人匹配',
  },
];

function buildIssueEntries(issues, entryMode) {
  // 混合入口：每個議題只有一張卡，導向不帶 mode 的路徑，由後端依立場分流。
  if (entryMode === 'mixed') {
    return issues.map((issue) => ({
      ...issue,
      entryKey: `${issue.id}-mixed`,
      mode: null,
      modeLabel: '開始對話',
      modeDescription: '填完立場問卷後自動安排對談對象',
    }));
  }

  return issues.flatMap((issue) =>
    ISSUE_ENTRY_MODES.map((mode) => ({
      ...issue,
      entryKey: `${issue.id}-${mode.key}`,
      mode: mode.key,
      modeLabel: mode.label,
      modeDescription: mode.description,
    })),
  );
}

function IssueCard({ navigate, issues, issuesLoaded, entryMode = 'split' }) {
  const entries = buildIssueEntries(issues, entryMode);
  const hasIssues = entries.length > 0;

  return (
    <div className="issue-highlight-card">
      <h3 className="card-title">開放中主題</h3>

      <div className="issue-list scrollable">
        {hasIssues ? (
          entries.map((issue) => (
            <div
              key={issue.entryKey}
              className="issue-item"
              onClick={() =>
                navigate(
                  issue.mode ? `/topic/${issue.id}?mode=${issue.mode}` : `/topic/${issue.id}`,
                )
              }
            >
              <div className="issue-primary">
                <div className="issue-title-row">
                  <span className="issue-title-text">{issue.title}</span>
                  <span className={`issue-mode-badge mode-${issue.mode || 'mixed'}`}>
                    {issue.modeLabel}
                  </span>
                </div>
                <span className="issue-mode-description">{issue.modeDescription}</span>
              </div>
              <span className="issue-timestamp">{issue.date}</span>
            </div>
          ))
        ) : (
          <div className="issue-item issue-item-empty" style={{ color: '#999', justifyContent: 'center' }}>
            {issuesLoaded ? '目前沒有可用議題' : '正在整理議題中...'}
          </div>
        )}
      </div>
    </div>
  );
}

export default IssueCard;
