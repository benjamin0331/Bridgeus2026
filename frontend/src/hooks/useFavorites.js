import { useCallback, useMemo, useState } from 'react';

const STORAGE_KEY = 'bridgeus_favorites_v1';

function readFavorites() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeFavorites(map) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // localStorage 不可用（例如無痕模式滿了）時，收藏狀態只留在當次畫面，不影響主要功能。
  }
}

// 收藏狀態目前只存在瀏覽器本機（後端尚未有收藏 model/endpoint），所以連同
// 卡片顯示用的欄位（摘要、標題…）一起存起來，收藏頁才不用額外打 API 把資料
// 撈回來。之後要接後端時，把 toggleFavorite 內部換成呼叫 API、favoriteItems
// 改讀後端回傳的清單即可，呼叫端（isFavorited/toggleFavorite/favoriteItems
// 介面）不用變。
export function useFavorites(kind) {
  const [favoritesByKind, setFavoritesByKind] = useState(() => readFavorites());

  const favoriteMap = useMemo(() => {
    const raw = favoritesByKind[kind];
    // 舊格式（純 id 陣列）沒有卡片資料可用，視同尚未收藏。
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  }, [favoritesByKind, kind]);

  const favoriteItems = useMemo(() => Object.values(favoriteMap), [favoriteMap]);

  const isFavorited = useCallback(
    (id) => Object.prototype.hasOwnProperty.call(favoriteMap, String(id)),
    [favoriteMap],
  );

  // item 需要有 id 欄位；已收藏時傳入任何帶正確 id 的物件即可移除收藏。
  const toggleFavorite = useCallback((item) => {
    const key = String(item.id);
    setFavoritesByKind((current) => {
      const currentMap = current[kind] ?? {};
      const nextMap = { ...currentMap };
      if (Object.prototype.hasOwnProperty.call(nextMap, key)) {
        delete nextMap[key];
      } else {
        nextMap[key] = item;
      }
      const next = { ...current, [kind]: nextMap };
      writeFavorites(next);
      return next;
    });
  }, [kind]);

  return { isFavorited, toggleFavorite, favoriteItems };
}
