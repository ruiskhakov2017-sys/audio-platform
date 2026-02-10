from django.urls import path
from .views import StoryListAPIView, StoryDetailAPIView

urlpatterns = [
    path('', StoryListAPIView.as_view(), name='story-list'),
    path('<slug:slug>/', StoryDetailAPIView.as_view(), name='story-detail'),
]