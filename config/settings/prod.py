from .base import *

DEBUG = False

ALLOWED_HOSTS = [
    config('AZURE_APP_URL'),   # your azure app url will go here
]

# Production Database — Azure PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': '5432',
        'OPTIONS': {
            'sslmode': 'require',   # Azure requires SSL
        },
    }
}


CSRF_TRUSTED_ORIGINS = [
    'https://apocorp-backend-fudtbranbbh8c4e5.centralindia-01.azurewebsites.net',
    'https://apo-corp-frontend.vercel.app',
    'https://apocorptech.com',
    'https://www.apocorptech.com',
]

SECURE_SSL_REDIRECT = False  # Azure handles SSL, not Django
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')  # Trust Azure's SSL
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# CORS — only allow your Vercel frontend
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    config('FRONTEND_URL'),   # your vercel URL
]

# Azure Blob Storage
# With this:
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.azure_storage.AzureStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

AZURE_ACCOUNT_NAME = config('AZURE_STORAGE_ACCOUNT_NAME')
AZURE_ACCOUNT_KEY = config('AZURE_STORAGE_ACCOUNT_KEY')
AZURE_CONTAINER = 'media'
AZURE_CUSTOM_DOMAIN = f'{AZURE_ACCOUNT_NAME}.blob.core.windows.net'
MEDIA_URL = f'https://{AZURE_CUSTOM_DOMAIN}/media/'

# prod.py
AZURE_OVERWRITE_FILES = True