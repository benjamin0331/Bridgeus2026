"""研究同意說明的版本與全文位置。

放程式碼常數而不是資料庫，理由與 api.dialogue_topics.TOPIC_CONFIGS、
api.achievements.CATALOG 一致：這是研究設計的一部分，不是使用者產生的內容，
沒有「線上新增一個版本」的需求，也就沒有理由付出 fixture 與 data migration
同步的代價。

改版規則：同意書內文一旦有實質變動就換一個新字串，不要沿用。
User.consent_version 存的是簽署當下的值，改這裡不會動到任何既有紀錄——
這正是要的行為，受試者同意的是他當時看到的那一版。
"""

CONSENT_VERSION = "2026-08-v1"

# 全文頁面的前端路徑，供註冊頁連結。內容由研究團隊提供。
CONSENT_DOCUMENT_PATH = "/consent"
