"""忘記密碼驗證碼信件。

內文是純文字常數（理由同 accounts/consent.py）：這是產品文案不是使用者
產生的內容，沒有線上編輯的需求。要 HTML 版再另外加。

dev 環境 settings.EMAIL_BACKEND 預設是 console backend，send_mail 只會把信
印到 stdout；正式機在 backend/.env 改成 smtp backend 並填 Gmail 應用程式
密碼才會真的寄出。
"""

from django.conf import settings
from django.core.mail import send_mail

SUBJECT = "【TakeAbridge】重設密碼驗證碼"

BODY_TEMPLATE = (
    "您好，\n\n"
    "我們收到重設 TakeAbridge 帳號密碼的請求。您的驗證碼是：\n\n"
    "    {code}\n\n"
    "驗證碼將在 {ttl_minutes} 分鐘後失效，且僅能使用一次。\n"
    "請回到頁面輸入驗證碼與新密碼以完成重設。\n\n"
    "如果這不是您本人的操作，請忽略這封信，您的密碼不會有任何變動。\n\n"
    "— TakeAbridge 團隊"
)


def send_password_reset_code(email: str, code: str) -> None:
    """把驗證碼寄到 email。寄失敗會讓 send_mail 拋例外，由呼叫端處理。"""
    body = BODY_TEMPLATE.format(
        code=code,
        ttl_minutes=settings.PASSWORD_RESET_CODE_TTL_MINUTES,
    )
    send_mail(
        SUBJECT,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
    )
