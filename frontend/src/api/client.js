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

export function getAccessTokenExpiry() {
  const token = localStorage.getItem('access');

  if (!token) {
    return null;
  }

  const payload = decodeJwtPayload(token);
  return typeof payload?.exp === 'number' ? payload.exp * 1000 : null;
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

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const requestUrl = error?.config?.url ?? '';
    const isAuthRequest = requestUrl.includes('/api/token/');

    if (status === 401 && !isAuthRequest) {
      dispatchAuthLogout('登入已過期，請重新登入。');
    }

    return Promise.reject(error);
  },
);

export default api;
