from django.db import models
from django.conf import settings


class Bookmark(models.Model):
    """Избранное: пользователь — история (уникальная пара)."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='story_bookmarks',
    )
    story_id = models.PositiveIntegerField(db_index=True)

    class Meta:
        verbose_name = "Избранное"
        verbose_name_plural = "Избранное"
        unique_together = [['user', 'story_id']]

    def __str__(self):
        return f"{self.user_id} -> story {self.story_id}"


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    is_premium = models.BooleanField("Подписка Premium", default=False)
    avatar = models.FileField("Аватар", upload_to="avatars/", blank=True, null=True)

    class Meta:
        verbose_name = "Профиль"
        verbose_name_plural = "Профили"

    def __str__(self):
        return f"{self.user.email} (premium={self.is_premium})"


class ContactRequest(models.Model):
    """Обратная связь с сайта (форма контактов)."""
    email = models.EmailField("Email")
    subject = models.CharField("Тема", max_length=200)
    message = models.TextField("Сообщение")
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        verbose_name = "Обращение"
        verbose_name_plural = "Обращения"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.email}: {self.subject[:50]}"
