from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        from . import signals  # noqa: F401  # 註冊 m2m_changed handler
