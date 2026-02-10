from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    # Подключаем наши рассказы по адресу /api/stories/
    path('api/stories/', include('stories.urls')),
]

# Это нужно, чтобы локально работала статика (если вдруг понадобится)
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
