# roadmap.md — Audio Streaming Platform

## 0) Инициализация проекта
- [x] Создать репозиторий и включить Git
- [x] Определить структуру: backend/ и frontend/ в корне
- [x] Завести .env для backend и frontend (без секретов в репо)

## 1) Настройка окружения
- [x] Установить Python 3.12+, Node.js 20 LTS, PostgreSQL, Git
- [x] Создать виртуальное окружение Python и активировать его
- [x] Установить Django, DRF, psycopg2, django-storages, simplejwt
- [x] Инициализировать Next.js (App Router) и Tailwind CSS

## 2) Бэкенд и база данных
- [x] Создать Django-проект
- [x] Настроить базовое подключение БД (SQLite для старта)
- [x] Сделать первичные миграции (admin, auth)
- [x] Создать суперпользователя и проверить админку
- [x] Создать приложение `stories` и зарегистрировать его
- [x] Создать модели (Genre, Author, Story)
- [x] Сделать миграции для stories и применить их
- [x] Зарегистрировать модели в Django Admin

## 3) S3-хранилище аудио и обложек
- [x] Подключить django-storages и boto3
- [x] Настроить S3 bucket, ключи и region через .env
- [x] Разделить хранилища для аудио и обложек (если нужно)
- [x] Проверить загрузку файла в админке и доступ по URL

## 4) API для фронтенда
- [x] Подключить DRF и настроить базовые настройки (pagination, filters)
- [x] Сделать serializers для Story, Genre, Author
- [x] Сделать endpoints:
  - [x] Список рассказов (с пагинацией, сортировкой)
  - [x] Детальная история по slug
  - [x] Список жанров
  - [x] Поиск по названию/автору/жанру
- [x] API freemium (см. §8): не отдавать URL аудио для premium без подписки “locked”

## 5) Авторизация (JWT)
- [x] Подключить djangorestframework-simplejwt
- [x] Настроить регистрацию и логин пользователей
- [x] Настроить refresh токены
- [x] Добавить эндпоинт “me” для профиля

## 6) Фронтенд: каркас
- [x] Подключить Tailwind и базовую тему UI
- [x] Страница главная: hero + сетка рассказов
- [x] Страница каталога: список + фильтр жанров
- [x] Страница рассказа: обложка, описание, плеер
- [x] Интеграция API (fetch/axios), loading/empty/error
- [x] UI авторизации: /login, /register, /profile, JWT в заголовках, кнопка «Выйти»
- [x] Каталог: синхронизация фильтров с URL (?search=…&genre=…)
- [x] Главная: «Вы недавно слушали» с прогрессом; тосты (sonner), скелетоны загрузки

## 7) Плеер (ключевой блок)
- [x] Глобальный плеер (музыка не прерывается при переходах по страницам)
- [x] UI плеера: play/pause, прогресс, таймер, скорость
- [x] Реализовать управление через HTML5 audio
- [x] Синхронизация прогресса и буфера
- [x] Сохранение позиции трека в localStorage
- [x] (Опционально) Синхронизация прогресса через API

## 8) Подписки и доступ к контенту
- [x] Модель: stories с флагом is_premium (бесплатные / premium)
- [x] UI: замок и CTA на premium-карточках, сообщение в плеере «Доступно по подписке»
- [x] Бэкенд: не отдавать URL аудио для premium без подписки (см. §4)
- [x] (Опционально) Подключить платежи (Mock готов, архитектурно готовы к Stripe/ЮKassa)

## 9) Вовлечение и юридическая база
- [x] Избранное (Bookmarks): модель, GET/POST API, кнопка «сердечко» на карточке, в плеере и в профиле («Мое Избранное» + /favorites с API)
- [x] Аватар в профиле: поле avatar, PATCH /api/auth/me/, загрузка на /profile, отображение в шапке
- [x] Страница тарифов /pricing (Start, Pro, Lifetime), кнопки «Выбрать», редирект по клику на замок
- [x] SEO: generateMetadata для /story/[id] (заголовок, description, Open Graph + og:image для шаринга)
- [x] Django Admin: list_display, list_filter (жанр, автор, premium), search_fields, миниатюры обложек, readonly created_at
- [x] Отзывы и рейтинг: модель Review, GET /reviews/?story_id=, POST /reviews/, блок на странице истории
- [x] Похожие истории: GET /api/stories/{slug}/related/, блок «Вам может понравиться»
- [x] Скорость воспроизведения: кнопка в плеере (1x → 1.25x → 1.5x → 2x)
- [x] Подвал сайта (Footer), страницы-заглушки /terms, /privacy, /about
- [x] Обратная связь: модель ContactRequest, POST /api/contact/, страница /contacts с формой

## 10) PWA, оплата, админка, сброс пароля, поиск
- [x] PWA: manifest.json (иконки, название, theme_color), мета viewport-fit=cover, apple-touch-icon, display standalone
- [x] Имитация оплаты: POST /api/payment/mock/ (plan_id, 2 сек, is_premium=True), кнопки на /pricing, лоадер, редирект в профиль
- [x] Дашборд статистики: GET /api/admin/stats/ (только is_staff), страница /admin/dashboard с карточками
- [x] Восстановление пароля: backend (console EmailBackend, password-reset + confirm), ссылка «Забыли пароль?», /forgot-password, /reset-password
- [x] Глобальный поиск в шапке: модалка по клику на лупу, live search по API, переход к истории по клику

## 11) Финальные проверки и релиз
- [x] Проверить безопасность .env и доступы к S3 (DEBUG=False по умолчанию, ALLOWED_HOSTS/CORS из env, SSL-куки)
- [x] Провести базовый QA: список/деталка/поиск/плеер
- [x] Настроить деплой: Supabase, Cloudflare, frontend (Vercel)
- [x] Настроить домен, HTTPS, резервные копии БД
- [x] Добавить мониторинг (Sentry: backend SENTRY_DSN, frontend @sentry/nextjs)
