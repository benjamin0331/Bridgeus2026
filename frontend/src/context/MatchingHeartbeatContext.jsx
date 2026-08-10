import { createContext, useCallback, useContext, useRef } from 'react';
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
        if (response.data?.status !== 'matching') {
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

  return (
    <MatchingHeartbeatContext.Provider value={{ keepAlive, release }}>
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
