from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0006_aisuggestion"),
    ]

    operations = [
        migrations.AddField(
            model_name="aisuggestion",
            name="response_time_ms",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aisuggestion",
            name="trigger_score",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aisuggestion",
            name="final_content",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aisuggestion",
            name="context_message_ids",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
