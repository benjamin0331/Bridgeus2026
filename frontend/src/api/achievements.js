import api from './client';

// 成就頁與成就通知共用同一支端點：GET 會順便結算（後端刻意的設計，見
// AchievementMeView 的 docstring），所以不需要另外的「刷新」呼叫。
export function fetchAchievements() {
  return api.get('/api/achievements/me/').then((response) => response.data);
}

// 通知跳完之後標記已讀。存在後端而不是 localStorage，換瀏覽器才不會重跳。
export function ackAchievements(codes) {
  if (!codes?.length) return Promise.resolve();
  return api.post('/api/achievements/ack/', { codes });
}
