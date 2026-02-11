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
DEBUG = os.environ.get('DEBUG', 'True').lower() in ('true', '1', 'yes')

# Хосты: локаль + *.railway.app + доп. из ALLOWED_HOSTS (через запятую)
_ALLOWED = ['localhost', '127.0.0.1', '.railway.app']
_extra = os.environ.get('ALLOWED_HOSTS', '').strip()
if _extra:
    _ALLOWED.extend(s.strip() for s in _extra.split(',') if s.strip())
ALLOWED_HOSTS = _ALLOWED

# CSRF: Vercel + Railway; доп. источники через CSRF_TRUSTED_ORIGINS (через запятую)
_CSRF = [
    'https://audio-platform-phi.vercel.app',
    'https://audio-platform-git-main-ruiskhakov2017-sys-projects.vercel.app',
]
_csrf_extra = os.environ.get('CSRF_TRUSTED_ORIGINS', '').strip()
if _csrf_extra:
    _CSRF.extend(s.strip() for s in _csrf_extra.split(',') if s.strip())
CSRF_TRUSTED_ORIGINS = _CSRF


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
# Разрешаем запросы с фронтенда (Next.js + Vercel + Railway)
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://audio-platform-phi.vercel.app",
    "https://audio-platform-git-main-ruiskhakov2017-sys-projects.vercel.app",
]
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://[\w\-]+\.railway\.app$",
]