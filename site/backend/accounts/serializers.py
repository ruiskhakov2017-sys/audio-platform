from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Profile

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
