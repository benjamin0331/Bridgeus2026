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
  // 後端 /api/token/ 的欄位名是 username，但值可以是帳號或 email——
  // BridgeUsTokenObtainPairSerializer 會把 email 換成對應的 username。
  return api.post('/api/token/', { username, password }).then(async (response) => {
    const { access, refresh } = response.data;
    // 先寫 access，下面那支 /api/me/ 才帶得到 Authorization。
    localStorage.setItem('access', access);

    // 使用者可能是用 email 登入的，這裡拿回真正的 username，否則首頁問候語
    // 與 changePassword() 會用到打字時輸入的 email。
    let resolvedUsername = username;
    try {
      const me = await api.get('/api/me/');
      if (me.data?.username) resolvedUsername = me.data.username;
    } catch {
      // 讀不到就退回使用者輸入的值，不擋登入。
    }

    return persistSession({ access, refresh, username: resolvedUsername });
  });
}

// 忘記密碼第一步：請後端把驗證碼寄到信箱。後端不論信箱有沒有註冊都回同一
// 句話（避免帳號列舉），所以前端也不用、也無法分辨成功與否，一律往下一步走。
export function requestPasswordReset({ email }) {
  return api
    .post('/api/password-reset/request/', { email })
    .then((response) => response.data);
}

// 忘記密碼第二步：驗證碼 + 新密碼。成功後端不發 token（不自動登入），
// 使用者拿新密碼回登入頁。
export function confirmPasswordReset({ email, code, newPassword, newPasswordConfirm }) {
  return api
    .post('/api/password-reset/confirm/', {
      email,
      code,
      new_password: newPassword,
      new_password_confirm: newPasswordConfirm,
    })
    .then((response) => response.data);
}

// 註冊前的信箱驗證：先寄碼、再驗碼。驗過之後後端才准 /api/register/ 建帳號
// （見 backend/accounts/serializers.py 的 validate_email）。
export function requestEmailVerification({ email }) {
  return api
    .post('/api/email-verification/request/', { email })
    .then((response) => response.data);
}

export function confirmEmailVerification({ email, code }) {
  return api
    .post('/api/email-verification/confirm/', { email, code })
    .then((response) => response.data);
}

// 登入後補驗自己目前的信箱（設定頁改了信箱、或研究者代開的帳號補上信箱）。
export function requestMyEmailVerification() {
  return api
    .post('/api/me/email/verify/request/', {})
    .then((response) => response.data);
}

export function confirmMyEmailVerification({ code }) {
  return api
    .post('/api/me/email/verify/confirm/', { code })
    .then((response) => response.data);
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

// 改密碼會讓後端作廢這個帳號既有的所有 refresh token（見
// backend/api/token_revocation.py），所以回應裡附了一組新的。不換掉
// localStorage 裡的舊 token，使用者會在 access token 過期後被自己剛做的
// 操作登出——體感像是「改密碼把我踢出去了」。
export function changePassword({ username, oldPassword, newPassword, newPasswordConfirm }) {
  return api
    .post('/api/me/password/', {
      old_password: oldPassword,
      new_password: newPassword,
      new_password_confirm: newPasswordConfirm,
    })
    .then((response) => {
      persistSession({
        access: response.data.access,
        refresh: response.data.refresh,
        username,
      });
      return response.data;
    });
}
