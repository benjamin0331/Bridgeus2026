import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0005_conversation_summary_stats"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AISuggestion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(choices=[("rephrase", "改述"), ("direction", "引導方向"), ("redirect", "回到主題")], max_length=20)),
                ("original_content", models.TextField(blank=True, null=True)),
                ("suggested_content", models.TextField()),
                ("user_action", models.CharField(blank=True, choices=[("accept", "接受"), ("modify", "修改"), ("ignore", "忽略")], max_length=10, null=True)),
                ("modified_content", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ai_suggestions", to="chat.conversation")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ai_suggestions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["created_at"],
                "indexes": [models.Index(fields=["conversation", "user", "created_at"], name="chat_ais_conv_user_idx")],
            },
        ),
    ]
