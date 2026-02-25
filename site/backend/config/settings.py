from pathlib import Path
import os
from dotenv import load_dotenv
import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env_path = BASE_DIR / '.env'
load_dotenv(env_path)

"""
Django settings for config project.
"""

# SECURITY: из переменных окружения (обязательно задать на Railway)
SECRET_KEY = os.environ.get(
    'SECRET_KEY',
    'django-insecure-dev-only-change-in-production'
)
DEBUG = os.environ.get('DEBUG', 'False').lower() in ('true', '1', 'yes')

# Хосты: из ALLOWED_HOSTS (через запятую) или дефолт локаль + Railway
_allowed_raw = os.environ.get('ALLOWED_HOSTS', '').strip()
if _allowed_raw:
    ALLOWED_HOSTS = [s.strip() for s in _allowed_raw.split(',') if s.strip()]
else:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', '.railway.app']

# CSRF: список из CSRF_TRUSTED_ORIGINS (через запятую) или дефолт для Vercel + Railway
_csrf_raw = os.environ.get('CSRF_TRUSTED_ORIGINS', '').strip()
if _csrf_raw:
    CSRF_TRUSTED_ORIGINS = [s.strip() for s in _csrf_raw.split(',') if s.strip()]
else:
    CSRF_TRUSTED_ORIGINS = [
        'https://audio-platform-phi.vercel.app',
        'https://audio-platform-git-main-ruiskhakov2017-sys-projects.vercel.app',
    ]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Сторонние библиотеки
    'rest_framework',
    'corsheaders',
    'storages',

    # Наши приложения
    'accounts',
    'stories',
]

# REST Framework + JWT
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
}

from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database: DATABASE_URL на проде (Railway), иначе SQLite локально
_db_default = {
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': BASE_DIR / 'db.sqlite3',
    'OPTIONS': {'timeout': 20},
}
_db_from_env = dj_database_url.config(conn_max_age=600)
DATABASES = {'default': _db_from_env or _db_default}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files: WhiteNoise раздаёт из STATIC_ROOT
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Настройки S3 (Cloudflare R2)
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME')
AWS_S3_ENDPOINT_URL = os.getenv('AWS_S3_ENDPOINT_URL')
AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'auto')

# ... (код выше не трогай)

# --- НОВЫЙ БЛОК S3 ---
import os
from dotenv import load_dotenv

# Принудительно загружаем .env файл из папки backend
env_path = BASE_DIR / '.env'
load_dotenv(env_path)

# Читаем ключи
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME')
AWS_S3_ENDPOINT_URL = os.getenv('AWS_S3_ENDPOINT_URL')
AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'auto')

# Проверка для отладки (выведется в терминал при запуске)
if not AWS_STORAGE_BUCKET_NAME:
    print("WARNING: Django не видит AWS_STORAGE_BUCKET_NAME в .env!")
else:
    print(f"SUCCESS: Django видит бакет: {AWS_STORAGE_BUCKET_NAME}")

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": AWS_ACCESS_KEY_ID,
            "secret_key": AWS_SECRET_ACCESS_KEY,
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "endpoint_url": AWS_S3_ENDPOINT_URL,
            "region_name": AWS_S3_REGION_NAME,
            "default_acl": None,
            "file_overwrite": False,
        },
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
# CORS: из переменной (через запятую) или дефолт
_cors_raw = os.environ.get('CORS_ALLOWED_ORIGINS', '').strip()
if _cors_raw:
    CORS_ALLOWED_ORIGINS = [s.strip() for s in _cors_raw.split(',') if s.strip()]
else:
    CORS_ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "https://audio-platform-phi.vercel.app",
        "https://audio-platform-git-main-ruiskhakov2017-sys-projects.vercel.app",
    ]
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://[\w\-]+\.railway\.app$",
]

# Продакшен: HTTPS и безопасные куки
# За прокси (Railway/Vercel) клиент уже на HTTPS; прокси шлёт X-Forwarded-Proto
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Email: SMTP из .env или консоль
_email_host = os.environ.get('EMAIL_HOST', '').strip()
_email_user = os.environ.get('EMAIL_HOST_USER', '').strip()
if _email_host and _email_user:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = _email_host
    EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
    EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'true').lower() in ('true', '1', 'yes')
    EMAIL_HOST_USER = _email_user
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
else:
    EMAIL_BACKEND = os.environ.get(
        'EMAIL_BACKEND',
        'django.core.mail.backends.console.EmailBackend'
    )
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:3000').rstrip('/')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@eroticaudio.local')

# Sentry (если задан SENTRY_DSN)
SENTRY_DSN = os.environ.get('SENTRY_DSN', '').strip()
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
        environment='production' if not DEBUG else 'development',
    )