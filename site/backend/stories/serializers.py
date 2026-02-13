from rest_framework import serializers
from .models import Story, Genre, Author, Review


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


class ReviewSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = Review
        fields = ['id', 'user', 'user_email', 'story', 'rating', 'text', 'created_at']
        read_only_fields = ['user', 'created_at']

    def validate_rating(self, value):
        if not (1 <= value <= 5):
            raise serializers.ValidationError('Оценка должна быть от 1 до 5.')
        return value

    def create(self, validated_data):
        user = self.context['request'].user
        story = validated_data['story']
        if Review.objects.filter(user=user, story=story).exists():
            raise serializers.ValidationError({'non_field_errors': ['Вы уже оставили отзыв на эту историю.']})
        validated_data['user'] = user
        return super().create(validated_data)
