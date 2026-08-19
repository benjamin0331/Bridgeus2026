import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import api from '../api/client';

const MatchingHeartbeatContext = createContext(null);

const POLL_INTERVAL_MS = 3000;

// 配對排隊心跳：GET /api/matching/status/ 除了回狀態，後端也會順便把該筆
// 排隊紀錄的 updated_at 刷新（見 matcher.py touch_matching_queue_entry）。
// 逾時沒刷新（預設 15 秒）後端就會判定放棄排隊、自動作廢紀錄。
//
// 這個 provider 掛在 App 根層級，跟頁面內元件的生命週期脫鉤：使用者離開
// 「配對中」畫面去看別頁時，TopicChat 會 unmount，但只要還在這個 SPA session
// 裡，這裡的輪詢仍會繼續刷新心跳，排隊不會被無聲判定逾時。真正結束排隊只有
// 兩種情況：使用者按下「取消配對」（呼叫 release 前會先打 cancel API），或
// 後端輪詢發現狀態已經不是 matching（配對成功 / 已被取消）時自行停止。
export function MatchingHeartbeatProvider({ children }) {
  const trackersRef = useRef(new Map()); // topicId(string) -> intervalId
  // 配對成功但使用者還沒點開查看的通知佇列（觸發彈出提醒 + 鈴鐺紅點）。
  // 只存在記憶體裡，跟這個 provider 本身一樣是 SPA session 範圍。
  const [matchAlerts, setMatchAlerts] = useState([]);

  const release = useCallback((topicId) => {
    const key = String(topicId);
    const intervalId = trackersRef.current.get(key);
    if (intervalId) {
      window.clearInterval(intervalId);
      trackersRef.current.delete(key);
    }
  }, []);

  const keepAlive = useCallback((topicId) => {
    const key = String(topicId ?? '');
    if (!key || trackersRef.current.has(key)) {
      return;
    }

    const poll = async () => {
      try {
        const response = await api.get(`/api/matching/status/?topic_id=${key}`);
        const data = response.data;
        if (data?.status === 'matched' && data?.room_id) {
          setMatchAlerts((current) => {
            if (current.some((alert) => alert.roomId === data.room_id)) {
              return current;
            }
            return [...current, {
              topicId: key,
              roomId: data.room_id,
              matchedAt: data.matched_at ?? null,
            }];
          });
        }
        if (data?.status !== 'matching') {
          release(key);
        }
      } catch (error) {
        if (error?.response?.status === 401) {
          release(key);
        }
        // 其餘錯誤視為暫時性網路問題，留給下一次輪詢重試，不主動放棄心跳。
      }
    };

    trackersRef.current.set(key, window.setInterval(poll, POLL_INTERVAL_MS));
    poll();
  }, [release]);

  const dismissMatchAlert = useCallback((roomId) => {
    setMatchAlerts((current) => current.filter((alert) => alert.roomId !== roomId));
  }, []);

  const value = useMemo(() => ({
    keepAlive,
    release,
    matchAlerts,
    dismissMatchAlert,
    hasMatchAlert: matchAlerts.length > 0,
  }), [keepAlive, release, matchAlerts, dismissMatchAlert]);

  return (
    <MatchingHeartbeatContext.Provider value={value}>
      {children}
    </MatchingHeartbeatContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- context hook lives alongside its provider
export function useMatchingHeartbeat() {
  const ctx = useContext(MatchingHeartbeatContext);
  if (!ctx) {
    throw new Error('useMatchingHeartbeat must be used within MatchingHeartbeatProvider');
  }
  return ctx;
}
