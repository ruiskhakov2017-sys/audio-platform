from rest_framework import generics
from .models import Story
from .serializers import StorySerializer

# 1. Список всех рассказов
class StoryListAPIView(generics.ListAPIView):
    queryset = Story.objects.all().order_by('-created_at')
    serializer_class = StorySerializer

# 2. Детальная информация об одном рассказе (по slug)
class StoryDetailAPIView(generics.RetrieveAPIView):
    queryset = Story.objects.all()
    serializer_class = StorySerializer
    lookup_field = 'slug'
