from django.contrib import admin
from django.utils.html import format_html
from .models import Genre, Author, Story, Review


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    list_filter = ('name',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name', 'bio')


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = ('cover_thumb', 'title', 'author', 'genre', 'duration', 'is_premium', 'created_at')
    list_filter = ('is_premium', 'genre', 'author', 'created_at')
    search_fields = ('title', 'description', 'author__name')
    prepopulated_fields = {'slug': ('title',)}
    list_editable = ('is_premium',)
    readonly_fields = ('created_at',)

    def cover_thumb(self, obj):
        if not obj.cover_image:
            return '—'
        url = obj.cover_image.url if obj.cover_image else ''
        if not url:
            return '—'
        return format_html(
            '<img src="{}" width="40" height="54" style="object-fit: cover; border-radius: 4px;" />',
            url,
        )
    cover_thumb.short_description = 'Обложка'


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('user', 'story', 'rating', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('user__email', 'story__title', 'text')