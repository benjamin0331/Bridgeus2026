import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import api from '../api/client';

const NotificationsContext = createContext(null);

// v2：存的是「上次看到時，這個房間總共有幾則訊息」而不是時間戳記，
// 才能算出「差幾則」當未讀數字用。跟 v1（時間戳記）的資料格式不同，
// 換個 key 避免舊資料被誤讀成數字做減法。
const LAST_SEEN_STORAGE_KEY = 'bridgeus_room_last_seen_v2';
const POLL_INTERVAL_MS = 20000;

function readLastSeenMap() {
  try {
    const raw = localStorage.getItem(LAST_SEEN_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeLastSeenMap(map) {
  try {
    localStorage.setItem(LAST_SEEN_STORAGE_KEY, JSON.stringify(map));
  } catch {
    // localStorage 不可用（例如無痕模式滿了）時，退化成當次連線內的已讀狀態，不影響主要功能。
  }
}

// 聊天室通知（真人配對室 + AI 對話都算）：輪詢歷史對話列表，比對「這個房間
// 目前的訊息總數」跟「使用者上次看過時的訊息總數」，差額就是未讀數字。
// 已讀基準存在 localStorage，不需要後端另外開表格追蹤已讀狀態。
export function NotificationsProvider({ user, children }) {
  const [rawRooms, setRawRooms] = useState([]);
  const [lastSeenMap, setLastSeenMap] = useState(() => readLastSeenMap());
  const pollTimerRef = useRef(null);

  const fetchRooms = useCallback(async () => {
    if (!user) {
      return;
    }
    try {
      const response = await api.get('/api/history/conversations/?type=all');
      const results = Array.isArray(response.data?.results) ? response.data.results : [];
      setRawRooms(results);
    } catch {
      // 通知輪詢失敗不影響主要功能，靜默重試即可。
    }
  }, [user]);

  useEffect(() => {
    if (!user) {
      return undefined;
    }

    // 這裡刻意在 effect 內再包一層本地函式呼叫 fetchRooms，而不是直接
    // `void fetchRooms()`——單純呼叫 useCallback 包過的版本會被
    // react-hooks/set-state-in-effect 誤判成「在 effect 裡同步呼叫
    // setState」，即使實際的 setRawRooms 是在 await 之後才執行。
    const poll = async () => {
      await fetchRooms();
    };

    void poll();
    pollTimerRef.current = window.setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollTimerRef.current) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [fetchRooms, user]);

  // messageCount：使用者「看到目前為止」這個房間總共有幾則訊息。
  const markRoomRead = useCallback((roomId, messageCount) => {
    if (!roomId || !Number.isFinite(messageCount)) {
      return;
    }
    setLastSeenMap((current) => {
      const existing = current[roomId] ?? 0;
      if (messageCount <= existing) {
        return current;
      }
      const next = { ...current, [roomId]: messageCount };
      writeLastSeenMap(next);
      return next;
    });
  }, []);

  // 幫每個房間補上 unreadCount：對方（或 AI）目前的訊息總數比使用者上次看到
  // 的還多，才算未讀，且只算差額——不是每次都整包重算成「未讀」。從沒看過
  // 的房間（map 裡沒有紀錄）保守只當作 1 則未讀，避免第一次看到就跳出一個
  // 嚇人的大數字。
  const rooms = useMemo(() => {
    return rawRooms.map((room) => {
      if (!room.last_message_from_partner) {
        return { ...room, unreadCount: 0 };
      }
      const seenCount = lastSeenMap[room.room_id];
      const totalCount = room.message_count || 0;
      const unreadCount = Number.isFinite(seenCount)
        ? Math.max(totalCount - seenCount, 0)
        : Math.min(totalCount, 1);
      return { ...room, unreadCount };
    });
  }, [lastSeenMap, rawRooms]);

  const unreadRooms = useMemo(
    () => rooms.filter((room) => room.unreadCount > 0),
    [rooms],
  );

  const value = useMemo(
    () => ({
      rooms,
      unreadRooms,
      hasUnread: unreadRooms.length > 0,
      markRoomRead,
      refresh: fetchRooms,
    }),
    [fetchRooms, markRoomRead, rooms, unreadRooms],
  );

  return (
    <NotificationsContext.Provider value={value}>
      {children}
    </NotificationsContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- context hook lives alongside its provider
export function useNotifications() {
  const context = useContext(NotificationsContext);
  if (!context) {
    throw new Error('useNotifications must be used within a NotificationsProvider');
  }
  return context;
}
