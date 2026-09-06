"""GET /api/consent/ — 研究參與說明全文端點。

重點：
  1. 免登入就能取得（受試者要在建立帳號前先看）。
  2. 回傳的 version 跟 accounts.consent.CONSENT_VERSION 是同一個值——註冊時
     記進 User.consent_version 的也是它，前端顯示的版本才對得起來。
  3. document 結構是前端 ConsentPage 依賴的形狀（sections / meta）。
"""

from rest_framework import status
from rest_framework.test import APITestCase

from accounts.consent import CONSENT_DOCUMENT, CONSENT_VERSION

CONSENT_URL = "/api/consent/"


class ConsentDocumentEndpointTests(APITestCase):
    def test_reachable_without_authentication(self):
        response = self.client.get(CONSENT_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_returns_version_matching_the_constant(self):
        response = self.client.get(CONSENT_URL)
        self.assertEqual(response.data["version"], CONSENT_VERSION)

    def test_returns_the_full_document_structure(self):
        response = self.client.get(CONSENT_URL)
        document = response.data["document"]

        self.assertEqual(document["title"], CONSENT_DOCUMENT["title"])
        self.assertEqual(document["eyebrow"], CONSENT_DOCUMENT["eyebrow"])

        self.assertTrue(len(document["sections"]) >= 1)
        for section in document["sections"]:
            self.assertIn("heading", section)
            self.assertIsInstance(section["paragraphs"], list)
            self.assertIsInstance(section["list_items"], list)
            # 每一節至少要有段落或條列其中之一，不會是空節。
            self.assertTrue(section["paragraphs"] or section["list_items"])

        for key in ("issuer", "published_date", "updated_date"):
            self.assertIn(key, document["meta"])
