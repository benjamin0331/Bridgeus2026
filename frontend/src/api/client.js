import axios from 'axios';

export const AUTH_LOGOUT_EVENT = 'bridgeus:auth-logout';

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
      } catch {
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
