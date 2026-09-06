import api from './client';

// GET /api/dialogue/topics/ 的 session 級快取。議題清單在一次瀏覽期間不會變，
// 但知識庫首頁、「觀看更多」頁、對話詳情頁都要用——共用同一次請求，之後切
// 頁面直接拿快取，不用再等網路。
let cache = null;
let inFlight = null;

export function getDialogueTopics() {
  if (cache) return Promise.resolve(cache);
  if (inFlight) return inFlight;

  inFlight = api
    .get('/api/dialogue/topics/')
    .then((response) => {
      cache = Array.isArray(response.data) ? response.data : [];
      return cache;
    })
    .catch(() => {
      // 不快取失敗結果：下次呼叫會重試。
      return [];
    })
    .finally(() => {
      inFlight = null;
    });

  return inFlight;
}
