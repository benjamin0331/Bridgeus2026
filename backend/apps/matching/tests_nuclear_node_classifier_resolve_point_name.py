"""Runs the real nuclear_node_classifier macro/micro models against sample
messages and prints the predicted class (大分類) and cluster (小分類), including
the "/"-compound cluster labels resolved via _resolve_point_name.
"""

from unittest import TestCase

from apps.matching.services import nuclear_node_classifier as m

SAMPLE_MESSAGES = [
    "核電會像輻射那樣有散射的風險對人造成危害嗎",
    "那照你這個說法聽起來核電沒甚麼壞處，反正安全穩定還能節省經濟成本",
    "政院估重啟耗時三年半 民團憂核安審查專業遭破壞",
    "阿我就只在乎電價阿",
]


class ClassifySampleMessagesTests(TestCase):
    """Runs the real macro/micro models (no mocking) on a handful of sample
    messages and prints the predicted class (大分類) and cluster (小分類) for
    manual inspection. Requires the model weights under
    `ml_models/nuclear_node_model/` to be present locally.
    """

    def test_prints_class_and_cluster_for_sample_messages(self):
        for text in SAMPLE_MESSAGES:
            with self.subTest(text=text):
                result = m.classify(text)
                point_name = (
                    m._resolve_point_name(text, result["cluster_id"], result["cluster_name"])
                    if result["cluster_id"] != -1
                    else None
                )
                print(
                    f"\n文字: {text}" 
                    f"\n大分類: {result['class_name']}\n"
                    f"小分類: {result['cluster_name']}\n"
                    f"拆解後節點名稱: {point_name}"
                )
                self.assertIn("class_id", result)
                self.assertIn("cluster_id", result)
