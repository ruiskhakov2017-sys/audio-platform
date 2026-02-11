from rest_framework import viewsets
from rest_framework.decorators import action
from django.db.models import Q
from .models import Story, Genre, Author
from .serializers import StorySerializer, GenreSerializer, AuthorSerializer


class StoryViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StorySerializer
    lookup_field = 'slug'
    lookup_url_kwarg = 'slug'

    def get_queryset(self):
        qs = Story.objects.select_related('genre', 'author').order_by('-created_at')
        genre = self.request.query_params.get('genre')
        author = self.request.query_params.get('author')
        search = self.request.query_params.get('search', '').strip()
        if genre:
            qs = qs.filter(Q(genre__slug=genre) | Q(genre__name__icontains=genre))
        if author:
            qs = qs.filter(author__name__icontains=author)
        if search:
            qs = qs.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )
        return qs.distinct()


class GenreViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Genre.objects.all().order_by('name')
    serializer_class = GenreSerializer
    lookup_field = 'slug'


class AuthorViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Author.objects.all().order_by('name')
    serializer_class = AuthorSerializer
