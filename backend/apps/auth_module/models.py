"""
M1 - User Auth Models
表面匿名、實質具名機制：顯示名稱為隨機代號，後台可追蹤真實身份。
"""
from django.contrib.auth.models import AbstractUser
from django.db import models
from core.models import TimestampedModel


class User(AbstractUser, TimestampedModel):
    """Extended user model with anonymized display name."""
    display_name = models.CharField(max_length=50, unique=True, blank=True)
    is_verified = models.BooleanField(default=False)

    class Meta:
        db_table = 'auth_users'

    def __str__(self):
        return self.display_name or self.username
