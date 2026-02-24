from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Profile, ContactRequest

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    email = serializers.EmailField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'password']

    def create(self, validated_data):
        email = validated_data.pop('email')
        password = validated_data.pop('password')
        username = email.split('@')[0]
        base_username = username
        n = 0
        while User.objects.filter(username=username).exists():
            n += 1
            username = f"{base_username}{n}"
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
        )
        Profile.objects.get_or_create(user=user, defaults={'is_premium': False})
        return user


class MeSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    is_premium = serializers.BooleanField(read_only=True)
    avatar_url = serializers.SerializerMethodField(read_only=True)

    def get_avatar_url(self, obj):
        profile = getattr(obj, 'profile', None)
        if not profile or not profile.avatar:
            return None
        request = self.context.get('request')
        url = profile.avatar.url
        if request and url and not url.startswith(('http://', 'https://')):
            return request.build_absolute_uri(url)
        return url    def to_representation(self, instance):
        profile = getattr(instance, 'profile', None)
        return {
            'id': instance.id,
            'username': instance.username,
            'email': getattr(instance, 'email', ''),
            'is_premium': profile.is_premium if profile else False,
            'avatar_url': self.get_avatar_url(instance),
        }


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """Для PATCH /api/auth/me/ (avatar)."""
    class Meta:
        model = Profile
        fields = ['avatar']


class ContactRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactRequest
        fields = ['email', 'subject', 'message']
