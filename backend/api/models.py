from django.db import models

# Create your models here.
# api/models.py

class AIConversation(models.Model):
    user_prompt = models.TextField(help_text="使用者的提問")
    ai_response = models.TextField(blank=True, null=True, help_text="AI 的回覆")
    created_at = models.DateTimeField(auto_now_add=True, help_text="建立時間")

    def __str__(self):
        return f"提問: {self.user_prompt[:20]}..."