from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import StoryViewSet, GenreViewSet, AuthorViewSet

router = DefaultRouter()
router.register(r'stories', StoryViewSet, basename='story')
router.register(r'genres', GenreViewSet, basename='genre')
router.register(r'authors', AuthorViewSet, basename='author')

urlpatterns = [
    path('', include(router.urls)),
]
