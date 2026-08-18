"""
URL configuration for BridgeUs_Django project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import path, include, re_path
from rest_framework_simplejwt.views import TokenRefreshView

from api.views import BridgeUsTokenObtainPairView
from .media_views import serve_media

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),  # 引入 api 部門網址
    path('api/', include('accounts.urls')),  # 自助註冊

    # JWT 通行證發放網址（BridgeUsTokenObtainPairView 在 access token 裡多帶 is_researcher claim）
    path('api/token/', BridgeUsTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]

# 本機上傳的影片檔（MEDIA_ROOT）：whitenoise 只認 STATIC_ROOT，這裡要另外
# 掛路徑才服務得到。用自己寫的 serve_media（見 media_views.py）而不是
# django.contrib.staticfiles/django.views.static.serve()提供的 static()
# helper——後者完全不支援 HTTP Range request，會讓 <video> 播放器沒辦法拖
# 時間軸、Chrome 甚至常常直接放棄解析音軌。只在 DEBUG 開著時掛這條路由，
# 正式環境如果換成 S3/GCS 之類的物件儲存，這段就不需要了（FileField 會
# 直接吐物件儲存本身、原生支援 Range 的網址）。
if settings.DEBUG:
    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            serve_media,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]