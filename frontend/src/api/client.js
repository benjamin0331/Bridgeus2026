import axios from 'axios';

export const AUTH_LOGOUT_EVENT = 'bridgeus:auth-logout';
export const AUTH_IDENTITY_CHANGED_EVENT = 'bridgeus:auth-identity-changed';

// ── 帳號一致性防線 ──────────────────────────────────────────────────
// 同一個瀏覽器的所有無痕／InPrivate 視窗**共用同一份 localStorage**（一個
// off-the-record profile），所以「開好幾個無痕視窗登不同帳號」並不會隔離
// 身分：後登入的那個會直接覆蓋 access/refresh/bridgeus_user，先前的視窗
// 從此帶著別人的 token 送請求，而畫面上完全看不出來。
//
// 症狀就是組員一直回報的那兩個：後端以 user_id 過濾，撈不到就回「找不到
// 對話 session，請重新建立對話」；後測問卷則被自己的帳號守衛擋下。
//
// 這裡記住「這個分頁登入時是誰」，任何請求送出前先比對一次。不相符就中止
// 請求並廣播事件，由 App 蓋上阻擋層——寧可讓使用者看到一個明確的畫面，也
// 不要讓一則發言以別人的身分落進實驗資料。
let anchoredUserId = null;

class AuthIdentityChangedError extends Error {
  constructor(currentUserId) {
    super('這個瀏覽器已切換到另一個帳號，請求已中止。');
    this.name = 'AuthIdentityChangedError';
    this.isAuthIdentityChanged = true;
    this.currentUserId = currentUserId;
  }
}

export { AuthIdentityChangedError };

function decodeJwtPayload(token) {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;

    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');

    return JSON.parse(window.atob(padded));
  } catch (error) {
    console.error('Failed to decode access token:', error);
    return null;
  }
}

export function clearAuthStorage() {
  localStorage.removeItem('access');
  localStorage.removeItem('refresh');
  localStorage.removeItem('bridgeus_user');
}

export function getAccessTokenPayload() {
  const token = localStorage.getItem('access');

  if (!token) {
    return null;
  }

  return decodeJwtPayload(token);
}

function readTokenUserId(token) {
  const payload = token ? decodeJwtPayload(token) : getAccessTokenPayload();
  const userId = payload?.user_id;
  return userId === undefined || userId === null ? null : String(userId);
}

// 登入成功、或帶著既有 token 重新載入分頁時呼叫：把「現在這個分頁是誰」定下來。
export function anchorAuthIdentity() {
  anchoredUserId = readTokenUserId();
  return anchoredUserId;
}

export function clearAuthIdentity() {
  anchoredUserId = null;
}

// 回傳「現在 localStorage 裡是哪個 user_id」，但只在它跟錨點不同時回傳；
// 相同或無從判斷（尚未錨定／已登出）時回 null。
export function detectIdentityChange() {
  if (anchoredUserId === null) {
    return null;
  }
  const currentUserId = readTokenUserId();
  if (currentUserId === null || currentUserId === anchoredUserId) {
    return null;
  }
  return currentUserId;
}

// 給「不經過 axios」的呼叫端用：WebSocket（token 走 query string）、
// 裸 fetch（離開頁面時的 keepalive 取消配對）、以及交棒給 Godot iframe 的
// token。這些路徑完全繞過上面的 request interceptor，是防線的缺口。
//
// 回傳 null 代表「現在沒有可用的 token」——與原本 `localStorage.getItem`
// 讀到 null 的意義一致，所有呼叫端既有的處理（顯示訊息／return／throw）
// 都不用改。身分不符時順便廣播事件，畫面會被 App 的阻擋層接管。
//
// 對 WS 重連迴圈是安全的：不建立 socket 就不會有 onclose，重連的 epoch
// 不會被加一，迴圈自然停住，不會變成無窮重試。
export function getVerifiedAccessToken() {
  const token = localStorage.getItem('access');
  if (!token) {
    return null;
  }

  const changedToUserId = detectIdentityChange();
  if (changedToUserId !== null) {
    dispatchIdentityChanged(changedToUserId);
    return null;
  }

  return token;
}

function dispatchIdentityChanged(currentUserId) {
  window.dispatchEvent(
    new CustomEvent(AUTH_IDENTITY_CHANGED_EVENT, {
      detail: { currentUserId, anchoredUserId },
    }),
  );
}

export function dispatchAuthLogout(message = '') {
  clearAuthStorage();
  window.dispatchEvent(
    new CustomEvent(AUTH_LOGOUT_EVENT, {
      detail: { message },
    }),
  );
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access');
  const requestUrl = config.url ?? '';
  const isAuthRequest = requestUrl.includes('/api/token/');

  if (token && !isAuthRequest) {
    // 送出前最後一道關：這個 token 已經不是這個分頁登入的那個人了，就不送。
    // 送出去的話，後端會忠實地以新帳號執行——輕則回 404 讓人摸不著頭緒，
    // 重則把一則發言或一份問卷記到錯的受試者身上。
    const changedToUserId = detectIdentityChange();
    if (changedToUserId !== null) {
      dispatchIdentityChanged(changedToUserId);
      return Promise.reject(new AuthIdentityChangedError(changedToUserId));
    }

    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }

  return config;
});

let refreshPromise = null;

// access token 只活 60 分鐘，但 refresh token 活 7 天；401 時先用 refresh
// token 換新的 access token 再重試一次，只有 refresh 本身失敗才真的登出，
// 避免使用者只是聊到一半就被踢出去。多個請求同時 401 時共用同一個
// refreshPromise，避免打好幾次 /api/token/refresh/。
function refreshAccessToken() {
  if (!refreshPromise) {
    const refreshToken = localStorage.getItem('refresh');
    if (!refreshToken) {
      return Promise.reject(new Error('No refresh token available'));
    }

    refreshPromise = axios
      .post(`${api.defaults.baseURL}/api/token/refresh/`, { refresh: refreshToken })
      .then((response) => {
        const newAccess = response.data?.access;
        if (!newAccess) {
          throw new Error('Refresh response missing access token');
        }
        // 這是整條防線裡最隱蔽的一段：refresh token 跟 access 一樣是共用
        // localStorage 的一個 key，另一個視窗登入後這裡讀到的已經是別人的。
        // 不檢查就把換回來的 access 寫進去，等於這個分頁靜悄悄變成另一個人，
        // 而且連 storage 事件都不會觸發（是我們自己寫的）。
        const newUserId = readTokenUserId(newAccess);
        if (anchoredUserId !== null && newUserId !== null && newUserId !== anchoredUserId) {
          dispatchIdentityChanged(newUserId);
          throw new AuthIdentityChangedError(newUserId);
        }
        localStorage.setItem('access', newAccess);
        return newAccess;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }

  return refreshPromise;
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const status = error?.response?.status;
    const originalConfig = error?.config;
    const requestUrl = originalConfig?.url ?? '';
    const isAuthRequest = requestUrl.includes('/api/token/');

    if (status === 401 && !isAuthRequest && originalConfig && !originalConfig._retry) {
      originalConfig._retry = true;

      try {
        const newAccess = await refreshAccessToken();
        originalConfig.headers = originalConfig.headers ?? {};
        originalConfig.headers.Authorization = `Bearer ${newAccess}`;
        return api(originalConfig);
      } catch (refreshError) {
        // 身分不符不是「登入過期」。走 dispatchAuthLogout 會清掉 localStorage，
        // 而那份 token 現在屬於另一個視窗裡正在使用的帳號——等於把別人也登出。
        if (refreshError?.isAuthIdentityChanged) {
          return Promise.reject(refreshError);
        }
        dispatchAuthLogout('登入已過期，請重新登入。');
        return Promise.reject(error);
      }
    }

    if (status === 401 && !isAuthRequest) {
      dispatchAuthLogout('登入已過期，請重新登入。');
    }

    return Promise.reject(error);
  },
);

export default api;
