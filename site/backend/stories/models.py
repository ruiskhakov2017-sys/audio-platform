from django.db import models
from django.utils.text import slugify

# 1. Жанры
class Genre(models.Model):
    name = models.CharField("Название", max_length=100)
    slug = models.SlugField(unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Жанр"
        verbose_name_plural = "Жанры"


# 2. Авторы
class Author(models.Model):
    name = models.CharField("Имя автора", max_length=200)
    bio = models.TextField("Биография", blank=True)
    
    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Автор"
        verbose_name_plural = "Авторы"


# 3. Рассказы
class Story(models.Model):
    title = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL-метка", unique=True, blank=True)
    description = models.TextField("Описание", blank=True)
    
    # Связи
    genre = models.ForeignKey(Genre, on_delete=models.SET_NULL, null=True, verbose_name="Жанр", related_name="stories")
    author = models.ForeignKey(Author, on_delete=models.SET_NULL, null=True, verbose_name="Автор", related_name="stories")

    # Файлы
    cover_image = models.FileField("Обложка", upload_to="covers/", blank=True, null=True)
    audio_file = models.FileField("Аудиофайл", upload_to="audio/")
    
    # Свойства
    duration = models.PositiveIntegerField("Длительность (сек)", default=0, help_text="Указывать в секундах")
    is_premium = models.BooleanField("Только по подписке", default=False)
    created_at = models.DateTimeField("Дата добавления", auto_now_add=True)
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "Рассказ"
        verbose_name_plural = "Рассказы"
        ordering = ['-created_at']
