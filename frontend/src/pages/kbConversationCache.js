import api from '../api/client';

// 「對話詳情頁」（KnowledgeBaseConversationPage）回應的模組級快取。
//
// 這支 API（GET /api/summary/viewpoints/:id/conversation/）第一次被打時，後端
// 會即時呼叫 LLM 生成對話摘要並存回去——所以「某場對話第一次有人點開」會明顯
// 比之後慢。用滑鼠移到卡片上就先預抓，把等待挪到 hover、不是挪到點擊之後；
// 已看過的再點就直接命中快取。
//
// 跨路由掛載存活。不做失效：詳情頁本身每次進來都會背景重抓一次刷新。

// viewpointId(字串) -> 詳情回應（或 null）
export const conversationCache = new Map();

// 進行中的預抓，避免 hover 抖動重複打。
const inFlight = new Map();

export function prefetchConversation(viewpointId) {
  const key = String(viewpointId);
  if (conversationCache.has(key)) return Promise.resolve();
  if (inFlight.has(key)) return inFlight.get(key);

  const promise = api
    .get(`/api/summary/viewpoints/${key}/conversation/`)
    .then((response) => {
      conversationCache.set(key, response.data ?? null);
    })
    .catch(() => {})
    .finally(() => {
      inFlight.delete(key);
    });

  inFlight.set(key, promise);
  return promise;
}
