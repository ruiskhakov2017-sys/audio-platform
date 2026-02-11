from rest_framework import generics
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import get_user_model
from .models import Profile
from .serializers import RegisterSerializer, MeSerializer

User = get_user_model()


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')
        if not email or not password:
            from rest_framework_simplejwt.exceptions import AuthenticationFailed
            raise AuthenticationFailed('Укажите email и пароль.')
        user = User.objects.filter(email__iexact=email).first()
        if not user or not user.check_password(password):
            from rest_framework_simplejwt.exceptions import AuthenticationFailed
            raise AuthenticationFailed('Неверный email или пароль.')
        attrs['username'] = user.username
        return super().validate(attrs)


class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer


class RegisterView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer
    queryset = User.objects.all()


class MeView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = MeSerializer

    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        is_premium = profile.is_premium if profile else False
        data = {
            'id': request.user.id,
            'username': request.user.username,
            'email': getattr(request.user, 'email', ''),
            'is_premium': is_premium,
        }
        return Response(data)
