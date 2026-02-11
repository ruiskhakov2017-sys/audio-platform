from django.urls import path
from .views import RegisterView, MeView, EmailTokenObtainPairView

urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth-register'),
    path('login/', EmailTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('me/', MeView.as_view(), name='auth-me'),
]
