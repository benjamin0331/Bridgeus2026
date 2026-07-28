from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0004_drift_model_and_initial_embeddings"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="summary",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="conversation",
            name="stats",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
