from django.contrib import admin
from .models import Genre, Author, Story

@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)} # Автоматически заполнять slug при вводе имени

@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ('name',)

@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'genre', 'duration', 'is_premium', 'created_at')
    list_filter = ('is_premium', 'genre', 'created_at')
    search_fields = ('title', 'author__name')
    prepopulated_fields = {'slug': ('title',)} # Авто-slug из названия
    list_editable = ('is_premium',) # Можно менять статус Premium прямо из списка