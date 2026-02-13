import time
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Sum
from .models import Profile, Bookmark, ContactRequest

try:
    from stories.models import Story
except ImportError:
    Story = None
from .serializers import RegisterSerializer, MeSerializer, ProfileUpdateSerializer, ContactRequestSerializer

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
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        profile = getattr(request.user, 'profile', None)
        is_premium = profile.is_premium if profile else False
        avatar_url = None
        if profile and profile.avatar:
            avatar_url = request.build_absolute_uri(profile.avatar.url) if profile.avatar.url and not profile.avatar.url.startswith(('http://', 'https://')) else profile.avatar.url
        data = {
            'id': request.user.id,
            'username': request.user.username,
            'email': getattr(request.user, 'email', ''),
            'is_premium': is_premium,
            'avatar_url': avatar_url,
        }
        return Response(data)

    def patch(self, request):
        profile = getattr(request.user, 'profile', None)
        if not profile:
            Profile.objects.create(user=request.user)
            profile = request.user.profile
        ser = ProfileUpdateSerializer(profile, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(MeSerializer(request.user, context={'request': request}).data)


class FavoritesListView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        story_ids = list(
            Bookmark.objects.filter(user=request.user).values_list('story_id', flat=True)
        )
        return Response({'story_ids': story_ids})


class ContactView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = ContactRequestSerializer
    queryset = ContactRequest.objects.all()


class MockPaymentView(APIView):
    """Тестовый платёж: задержка 2 сек, выставляет is_premium=True."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        plan_id = request.data.get('plan_id') or request.query_params.get('plan_id')
        time.sleep(2)
        profile = getattr(request.user, 'profile', None)
        if not profile:
            profile = Profile.objects.create(user=request.user)
        profile.is_premium = True
        profile.save()
        return Response({'success': True})


class AdminStatsView(APIView):
    """Статистика для админа (только is_staff)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        total_users = User.objects.count()
        premium_users = Profile.objects.filter(is_premium=True).count()
        total_stories = Story.objects.count() if Story else 0
        total_listen_seconds = 0
        if Story:
            agg = Story.objects.aggregate(s=Sum('duration'))
            total_listen_seconds = agg.get('s') or 0
        return Response({
            'total_users': total_users,
            'premium_users': premium_users,
            'total_stories': total_stories,
            'total_listen_seconds': total_listen_seconds,
        })


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get('email') or '').strip()
        if not email:
            return Response({'detail': 'Email required'}, status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response({'detail': 'If this email exists, you will receive a reset link.'})
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
        reset_link = f'{frontend_url}/reset-password?uid={uid}&token={token}'
        send_mail(
            subject='Сброс пароля — EroticAudio',
            message=f'Перейдите по ссылке для сброса пароля:\n{reset_link}\n\nСсылка действительна 24 часа.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return Response({'detail': 'If this email exists, you will receive a reset link.'})


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get('uid') or request.query_params.get('uid')
        token = request.data.get('token') or request.query_params.get('token')
        new_password = request.data.get('new_password') or request.data.get('password')
        if not all([uid, token, new_password]):
            return Response(
                {'detail': 'uid, token and new_password required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if len(new_password) < 8:
            return Response(
                {'detail': 'Password must be at least 8 characters'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            user_id = urlsafe_base64_decode(uid).decode()
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response({'detail': 'Invalid or expired link'}, status=status.HTTP_400_BAD_REQUEST)
        if not default_token_generator.check_token(user, token):
            return Response({'detail': 'Invalid or expired link'}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new_password)
        user.save()
        return Response({'detail': 'Password has been reset.'})
