import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react';
import api from '../api/client';

// 收藏狀態現在由後端 /api/favorites/ 存放（跨裝置同步），這裡只是一個小型
// module-level store：同一頁常常會有兩個 useFavorites('viewpoint') /
// useFavorites('video') 實例（見 KnowledgeBase.jsx、FavoritesPage.jsx），
// 用共享 store 讓它們共用同一次 GET，並且互相看得到彼此的樂觀更新。
let state = {
  viewpoint: new Map(),
  video: new Map(),
  status: 'idle', // idle | loading | loaded | error
};
let listeners = new Set();
let inFlightFetch = null;
// 正在等待回應的 toggle（`${kind}:${id}`），避免同一個目標連點時競態。
const inFlightToggles = new Set();

function emitChange() {
  listeners.forEach((listener) => listener());
}

function setState(partial) {
  state = { ...state, ...partial };
  emitChange();
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return state;
}

// 登出時呼叫，避免下一個在同一頁面登入的使用者看到前一個人的收藏。
export function resetFavoritesStore() {
  inFlightFetch = null;
  inFlightToggles.clear();
  setState({ viewpoint: new Map(), video: new Map(), status: 'idle' });
}

function ensureLoaded() {
  if (state.status === 'loaded' || state.status === 'loading') {
    return inFlightFetch ?? Promise.resolve();
  }

  setState({ status: 'loading' });
  inFlightFetch = api
    .get('/api/favorites/')
    .then((response) => {
      const data = response.data || {};
      const viewpointMap = new Map((data.viewpoint || []).map((item) => [String(item.id), item]));
      const videoMap = new Map((data.video || []).map((item) => [String(item.id), item]));
      setState({ viewpoint: viewpointMap, video: videoMap, status: 'loaded' });
    })
    .catch((error) => {
      // 不要靜靜消失：畫面會停在「還沒有收藏」，跟真的沒收藏長得一模一樣，
      // console 至少要留下線索（同 LevelSummaryCard 的作法）。
      console.warn('收藏清單讀取失敗：', error?.message ?? error);
      setState({ status: 'error' });
    })
    .finally(() => {
      inFlightFetch = null;
    });

  return inFlightFetch;
}

// item 需要有 id 欄位；已收藏時傳入任何帶正確 id 的物件即可移除收藏。
export function useFavorites(kind) {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot);

  // 依賴 status 而不是 []：登出時 resetFavoritesStore() 把狀態打回 'idle'，
  // 如果這裡只在掛載時跑一次，登出後在同一個頁面重新登入、而元件沒有被卸載
  // 過的話就永遠不會重抓，收藏頁會一直是空的。'error' 不會回到 'idle'，
  // 所以不會變成無限重試。
  useEffect(() => {
    if (snapshot.status === 'idle') {
      ensureLoaded();
    }
  }, [snapshot.status]);

  const favoriteMap = snapshot[kind];

  const favoriteItems = useMemo(() => Array.from(favoriteMap.values()), [favoriteMap]);

  const isFavorited = useCallback(
    (id) => favoriteMap.has(String(id)),
    [favoriteMap],
  );

  const toggleFavorite = useCallback((item) => {
    const key = String(item.id);
    const inFlightKey = `${kind}:${key}`;
    // 後端 POST 是 toggle 語意，所以連點兩下會送出兩個方向相反的請求，而回應
    // 不保證照送出順序回來——先回的那個會覆寫後回的，畫面就跟資料庫對不上。
    // 同一個目標在請求還沒回來之前直接忽略後續點擊。
    if (inFlightToggles.has(inFlightKey)) return;
    inFlightToggles.add(inFlightKey);

    const beforeMap = state[kind];
    const wasFavorited = beforeMap.has(key);

    // 樂觀更新：先切星星，API 回來再用實際結果校正，避免點擊到 API 回應
    // 之間的空檔感覺卡頓。
    const optimisticMap = new Map(beforeMap);
    if (wasFavorited) {
      optimisticMap.delete(key);
    } else {
      optimisticMap.set(key, item);
    }
    setState({ [kind]: optimisticMap });

    api
      .post('/api/favorites/', { target_type: kind, target_id: item.id })
      .then((response) => {
        const favorited = Boolean(response.data?.favorited);
        const nextMap = new Map(state[kind]);
        if (favorited) {
          nextMap.set(key, item);
        } else {
          nextMap.delete(key);
        }
        setState({ [kind]: nextMap });
      })
      .catch(() => {
        // 失敗就退回切換前的狀態。
        const revertMap = new Map(state[kind]);
        if (wasFavorited) {
          revertMap.set(key, item);
        } else {
          revertMap.delete(key);
        }
        setState({ [kind]: revertMap });
      })
      .finally(() => {
        inFlightToggles.delete(inFlightKey);
      });
  }, [kind]);

  return { isFavorited, toggleFavorite, favoriteItems, isLoading: snapshot.status === 'loading' };
}
