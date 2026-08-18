import api, { getAccessTokenPayload } from './client';

// 登入與註冊成功後要做的事完全一樣：存 token、組出前端用的 user 物件、
// 寫進 localStorage。抽出來共用，避免兩個頁面各自維護一份而長歪。
export function persistSession({ access, refresh, username }) {
  localStorage.setItem('access', access);
  localStorage.setItem('refresh', refresh);

  const tokenPayload = getAccessTokenPayload();
  const nextUser = {
    name: username,
    username,
    id: tokenPayload?.user_id ?? username,
    // 只用來決定前端要不要顯示研究者專用連結（例如 /viewpoint-review）；
    // 實際的存取控制一律由後端 IsResearcher 把關，這裡不是安全邊界。
    isResearcher: Boolean(tokenPayload?.is_researcher),
  };

  localStorage.setItem('bridgeus_user', JSON.stringify(nextUser));
  return nextUser;
}

export function login({ username, password }) {
  // Django 預設的欄位名是 username。
  return api.post('/api/token/', { username, password }).then((response) => {
    return persistSession({
      access: response.data.access,
      refresh: response.data.refresh,
      username,
    });
  });
}

export function register(payload) {
  return api.post('/api/register/', payload).then((response) => {
    return persistSession({
      access: response.data.access,
      refresh: response.data.refresh,
      username: response.data.user.username,
    });
  });
}
