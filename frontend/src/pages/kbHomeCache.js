import api from '../api/client';
import { getDialogueTopics } from '../api/dialogueTopics';

// 知識庫首頁（KnowledgeBase）「熱門對話」小卡與「影片推薦」的模組級快取。
//
// 拉到模組層（而非 KnowledgeBase 元件內的 useRef）的理由：使用者還在站內
// 首頁時就能先把資料備好（見 prefetchKnowledgeBaseHome），進 /kb 直接命中、
// 不用等網路。快取跨路由掛載存活；KnowledgeBase 每次仍會背景重抓刷新
// （stale-while-revalidate），過時資料自癒。

export const ALL_TOPIC_ID = 'all';
// 首頁每個分類的「熱門對話」小卡預設顯示這麼多筆，其餘走「觀看更多」。
export const TOP_CONVERSATIONS_LIMIT = 3;

// "全部"不帶 topic_id：highlights/videos 兩支端點本來就支援不篩 topic。
const topicParamsFor = (id) => (id === ALL_TOPIC_ID ? {} : { topic_id: id });

// key = 分類 id 字串 -> rows 陣列
export const highlightsCache = new Map();
export const videosCache = new Map();

export function fetchHighlights(topicId) {
  return api
    .get('/api/summary/viewpoints/highlights/', {
      params: { ...topicParamsFor(topicId), limit: TOP_CONVERSATIONS_LIMIT },
    })
    .then((response) => {
      const rows = Array.isArray(response.data) ? response.data : [];
      highlightsCache.set(String(topicId), rows);
      return rows;
    });
}

export function fetchVideos(topicId) {
  return api
    .get('/api/summary/videos/', { params: topicParamsFor(topicId) })
    .then((response) => {
      const rows = Array.isArray(response.data) ? response.data : [];
      videosCache.set(String(topicId), rows);
      return rows;
    });
}

function makePrefetch(cache, fetcher) {
  const inFlight = new Map();
  return (topicId) => {
    const key = String(topicId);
    if (cache.has(key)) return Promise.resolve();
    if (inFlight.has(key)) return inFlight.get(key);
    const promise = fetcher(topicId)
      .catch(() => {})
      .finally(() => inFlight.delete(key));
    inFlight.set(key, promise);
    return promise;
  };
}

export const prefetchHighlights = makePrefetch(highlightsCache, fetchHighlights);
export const prefetchVideos = makePrefetch(videosCache, fetchVideos);

// 使用者還在站內首頁時就把「全部」分類（/kb 一進去的預設畫面）備好。
export function prefetchKnowledgeBaseHome() {
  getDialogueTopics();
  prefetchHighlights(ALL_TOPIC_ID);
  prefetchVideos(ALL_TOPIC_ID);
}
