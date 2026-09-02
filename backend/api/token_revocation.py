"""作廢某個使用者既有的 JWT。

密碼一換，舊憑證就該失效——否則「改密碼」對「我的密碼可能外流了」這個
使用情境毫無作用：simplejwt 的 refresh token 預設活 7 天，access token 60
分鐘，兩者都不會因為 User.password 變了而失效。

**已知界線：只有 refresh token 擋得住。** blacklist 是在 RefreshToken 解碼時
比對的，access token 走的是純簽章驗證、不查 DB，所以舊的 access token 仍會
活到自然過期（`JWT_ACCESS_TOKEN_LIFETIME_MINUTES`，預設 60）。要連 access
也即時失效就得每次請求查 DB，那個成本這個專案不划算；縮短 access 壽命是
比較便宜的調節旋鈕。
"""

from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)


def revoke_user_tokens(user) -> int:
    """把這個 user 目前所有 outstanding 的 refresh token 加入黑名單。

    回傳這次新加入黑名單的張數（已在黑名單上的不重複計算）。
    """
    revoked = 0
    for token in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        if created:
            revoked += 1
    return revoked
