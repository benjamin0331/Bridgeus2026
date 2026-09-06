// 「觀看更多」（KnowledgeBaseTopicPage）的資料快取，供 SWR 與首頁「觀看更多」
// 按鈕的預抓共用。
//
// 分頁改成「一次抓回整個議題的觀點、前端依『卡片（同一場對話分組後）』每頁
// CARDS_PER_PAGE 張切」——這樣每頁一定是滿的 CARDS_PER_PAGE 張，只有最後一頁
// 才會不足；翻頁也不用再打 API。因此快取 key 只有議題、不含頁碼。
//
// 跨路由掛載存活（模組作用域），所以從首頁預抓的清單，點進「觀看更多」就
// 直接命中。key 用樣板字串，topicId 是數字或字串都對得起來
// （`${102}` === `${'102'}`）。不做失效：每次進頁面都會背景重抓一次刷新，
// 過時資料下次瀏覽就自癒。

// 「觀看更多」頁每頁顯示幾張卡片（所有分類共用）。
export const CARDS_PER_PAGE = 6;

// 一次向後端要多少筆觀點（分組成卡片之前的原始筆數）。對齊後端
// ViewpointBrowsePagination.max_page_size；某議題的已審核觀點超過這個數才需要
// 調大兩邊。
export const BROWSE_FETCH_SIZE = 500;

export const browseCacheKey = (topicId) => `${topicId}`;

// key -> viewpoint rows 陣列（分組成卡片前的原始清單）
export const browseCache = new Map();
