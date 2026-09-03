"""前測立場問卷改成 append-only：拿掉 per user+topic 的唯一約束。

在此之前受試者每為一場新對話重填問卷，就用 update_or_create 把上一次的
survey_answers / survey_open_answers / q9_embedding 蓋掉——那份正是上一場對話
的 s_pre 依據，蓋掉之後 D1↔Q10 的向量比較與立場漂移基準線都對不回去。

既有資料不動：每個 user+topic 現有的那一列原樣留著，成為該組的第一列。

不可逆。反向遷移要重新建立唯一約束，但那需要先刪掉每組除了最新以外的所有列，
也就是刪掉這次要保住的研究資料——與其安靜地毀掉它，不如讓 migrate 失敗。
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0036_policyidea'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='userstanceprofile',
            name='uniq_stance_profile_user_topic',
        ),
        migrations.AddIndex(
            model_name='userstanceprofile',
            index=models.Index(fields=['user', 'topic_id', '-created_at'], name='stance_profile_latest_idx'),
        ),
    ]
