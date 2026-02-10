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
    # Вкладываем жанр и автора внутрь, чтобы сразу получить их имена, а не просто ID
    genre = GenreSerializer(read_only=True)
    author = AuthorSerializer(read_only=True)

    class Meta:
        model = Story
        fields = [
            'id', 'title', 'slug', 'description', 
            'genre', 'author', 
            'cover_image', 'audio_file', 
            'duration', 'is_premium', 'created_at'
        ]