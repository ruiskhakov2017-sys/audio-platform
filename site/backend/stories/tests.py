from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from .models import Genre, Author, Story
from accounts.models import Profile

User = get_user_model()


class StoryAPITestCase(APITestCase):
    """Тесты бизнес-логики: публичная история, premium lock."""

    def setUp(self):
        self.genre = Genre.objects.create(name='Драма', slug='drama')
        self.author = Author.objects.create(name='Тест Автор', bio='')
        self.story_free = Story.objects.create(
            title='Бесплатная история',
            slug='free-story',
            description='Описание',
            genre=self.genre,
            author=self.author,
            duration=120,
            is_premium=False,
        )
        self.story_premium = Story.objects.create(
            title='Премиум история',
            slug='premium-story',
            description='Описание премиум',
            genre=self.genre,
            author=self.author,
            duration=180,
            is_premium=True,
        )

    def test_public_story_anyone_can_get_detail(self):
        """Любой пользователь (в т.ч. аноним) может получить детали бесплатной истории."""
        response = self.client.get(f'/api/stories/{self.story_free.slug}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['slug'], self.story_free.slug)
        self.assertEqual(response.data['title'], self.story_free.title)
        self.assertFalse(response.data['is_premium'])
        # У бесплатной истории аудио-URL отдаётся (может быть пустая строка если нет файла)
        self.assertIn('audio_file_url', response.data)

    def test_premium_lock_anonymous_no_audio_url(self):
        """Аноним при запросе премиум-истории получает урезанные данные: без ссылки на аудио."""
        response = self.client.get(f'/api/stories/{self.story_premium.slug}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_premium'])
        self.assertEqual(response.data['audio_file_url'], '')
        self.assertEqual(response.data['slug'], self.story_premium.slug)


class AuthMeAPITestCase(APITestCase):
    """Проверка доступа к /api/auth/me/."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
        )
        Profile.objects.get_or_create(user=self.user, defaults={'is_premium': False})

    def test_me_without_token_returns_401(self):
        """Без токена эндпоинт /api/auth/me/ возвращает 401."""
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_with_token_returns_200(self):
        """С валидным токеном /api/auth/me/ возвращает 200 и данные пользователя."""
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('email', response.data)
        self.assertIn('id', response.data)
