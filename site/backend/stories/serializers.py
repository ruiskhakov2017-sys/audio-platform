from rest_framework import serializers
from .models import Story, Genre, Author


class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ['id', 'name', 'slug']


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ['id', 'name', 'bio']


class StorySerializer(serializers.ModelSerializer):
    genre = GenreSerializer(read_only=True)
    author = AuthorSerializer(read_only=True)
    cover_image_url = serializers.SerializerMethodField()
    audio_file_url = serializers.SerializerMethodField()

    class Meta:
        model = Story
        fields = [
            'id', 'title', 'slug', 'description',
            'genre', 'author',
            'cover_image', 'audio_file',
            'cover_image_url', 'audio_file_url',
            'duration', 'is_premium', 'created_at',
        ]

    def get_cover_image_url(self, obj):
        if not obj.cover_image:
            return None
        url = obj.cover_image.url
        request = self.context.get('request')
        if request and url and not url.startswith(('http://', 'https://')):
            return request.build_absolute_uri(url)
        return url

    def get_audio_file_url(self, obj):
        if not obj.audio_file:
            return None
        request = self.context.get('request')
        if obj.is_premium and request:
            user = getattr(request, 'user', None)
            if not user or not user.is_authenticated:
                return ''
            profile = getattr(user, 'profile', None)
            if not profile or not profile.is_premium:
                return ''
        url = obj.audio_file.url
        if request and url and not url.startswith(('http://', 'https://')):
            return request.build_absolute_uri(url)
        return url
