import random
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.db.models import Q
from accounts.models import Bookmark
from .models import Story, Genre, Author, Review
from .serializers import StorySerializer, GenreSerializer, AuthorSerializer, ReviewSerializer


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

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def favorite(self, request, slug=None):
        story = self.get_object()
        bookmark, created = Bookmark.objects.get_or_create(
            user=request.user,
            story_id=story.id,
        )
        if not created:
            bookmark.delete()
            is_favorite = False
        else:
            is_favorite = True
        return Response({'is_favorite': is_favorite})

    @action(detail=True, methods=['get'], url_path='related', permission_classes=[AllowAny])
    def related(self, request, slug=None):
        story = self.get_object()
        genre = story.genre
        qs = Story.objects.select_related('genre', 'author').exclude(pk=story.pk).order_by('-created_at')
        if genre:
            qs = qs.filter(genre=genre)
        ids = list(qs.values_list('pk', flat=True)[:20])
        random.shuffle(ids)
        selected = ids[:4]
        if not selected:
            qs = Story.objects.select_related('genre', 'author').exclude(pk=story.pk).order_by('-created_at')[:4]
        else:
            qs = Story.objects.filter(pk__in=selected).select_related('genre', 'author')
            qs = sorted(qs, key=lambda s: selected.index(s.pk))
        serializer = StorySerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)


class ReviewViewSet(viewsets.ModelViewSet):
    serializer_class = ReviewSerializer
    permission_classes = [AllowAny]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_permissions(self):
        if self.action == 'create':
            return [IsAuthenticated()]
        return [AllowAny()]

    def get_queryset(self):
        qs = Review.objects.select_related('user', 'story').order_by('-created_at')
        story_id = self.request.query_params.get('story_id')
        if story_id:
            qs = qs.filter(story_id=story_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class GenreViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Genre.objects.all().order_by('name')
    serializer_class = GenreSerializer
    lookup_field = 'slug'


class AuthorViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Author.objects.all().order_by('name')
    serializer_class = AuthorSerializer
