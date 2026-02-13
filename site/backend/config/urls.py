from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from accounts.views import ContactView, MockPaymentView, AdminStatsView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/contact/', ContactView.as_view(), name='contact'),
    path('api/payment/mock/', MockPaymentView.as_view(), name='payment-mock'),
    path('api/admin/stats/', AdminStatsView.as_view(), name='admin-stats'),
    path('api/', include('stories.urls')),
]

# Это нужно, чтобы локально работала статика (если вдруг понадобится)
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
