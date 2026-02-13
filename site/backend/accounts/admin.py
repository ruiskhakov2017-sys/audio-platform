from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Profile, Bookmark, ContactRequest

User = get_user_model()


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'is_premium', 'avatar']
    list_filter = ['is_premium']


@admin.register(Bookmark)
class BookmarkAdmin(admin.ModelAdmin):
    list_display = ['user', 'story_id']
    list_filter = ['user']
    search_fields = ['user__email', 'user__username']


@admin.register(ContactRequest)
class ContactRequestAdmin(admin.ModelAdmin):
    list_display = ['email', 'subject', 'created_at']
    list_filter = ['created_at']
    search_fields = ['email', 'subject', 'message']


class CustomUserAdmin(BaseUserAdmin):
    list_display = BaseUserAdmin.list_display + ('is_premium_display',)
    list_filter = BaseUserAdmin.list_filter
    search_fields = ['email', 'username']

    def is_premium_display(self, obj):
        p = getattr(obj, 'profile', None)
        return p.is_premium if p else False
    is_premium_display.boolean = True
    is_premium_display.short_description = 'Premium'


if admin.site.is_registered(User):
    admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
